"""Shared-authority contracts for dashboard settings."""
from __future__ import annotations

import asyncio
import copy
import threading
import uuid
from types import SimpleNamespace

import pytest
from maverick.authority_documents import AuthorityDocumentStore
from maverick.dashboard_settings_authority import DashboardSettingsAuthority
from maverick.privacy_ops import RecordConflict


class _TenantMemoryBackend:
    """Postgres-shaped CAS backend keyed by the active tenant."""

    kind = "postgres"
    shared = True

    def __init__(self, *, race_first_reads: bool = False) -> None:
        self.records: dict[tuple[str, str], dict] = {}
        self.lock = threading.Lock()
        self.audit_actions: list[str] = []
        self._read_barrier = threading.Barrier(2) if race_first_reads else None
        self._barrier_reads = 0

    @staticmethod
    def _tenant() -> str:
        from maverick.paths import current_tenant_id_strict

        tenant = current_tenant_id_strict()
        if tenant is None:  # pragma: no cover - authority rejects this first
            raise AssertionError("test backend was used without a tenant")
        return tenant

    def _key(self, record_id: str) -> tuple[str, str]:
        return self._tenant(), record_id

    def new_id(self) -> str:
        return f"DSS-{uuid.uuid4().hex}"

    def save(self, record, *, expected_revision=None):
        key = self._key(record["id"])
        with self.lock:
            current = self.records.get(key)
            revision = int((current or {}).get("revision", 0))
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(record)
            saved["revision"] = revision + 1
            saved.setdefault("created_at", 1.0)
            saved.setdefault("updated_at", 1.0)
            self.records[key] = saved
            return copy.deepcopy(saved)

    def load(self, record_id):
        key = self._key(record_id)
        barrier = None
        with self.lock:
            value = self.records.get(key)
            if (
                value is None
                and self._read_barrier is not None
                and self._barrier_reads < 2
            ):
                self._barrier_reads += 1
                barrier = self._read_barrier
            result = copy.deepcopy(value) if value is not None else None
        if barrier is not None:
            barrier.wait(timeout=5)
        return result

    def list(self):
        tenant = self._tenant()
        with self.lock:
            return [
                copy.deepcopy(value)
                for (owner, _record_id), value in self.records.items()
                if owner == tenant
            ]

    def iter_record_ids(self, *, start_after="", limit=100):
        tenant = self._tenant()
        with self.lock:
            values = sorted(
                record_id
                for owner, record_id in self.records
                if owner == tenant and record_id > start_after
            )
        for record_id in values[:limit]:
            yield record_id, record_id

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        key = self._key(record_id)
        with self.lock:
            current = self.records.get(key)
            if current is None:
                return None
            revision = int(current["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(current)
            mutate(saved)
            saved["revision"] = revision if metadata_only else revision + 1
            self.records[key] = saved
            return copy.deepcopy(saved)


def _authority(backend: _TenantMemoryBackend) -> DashboardSettingsAuthority:
    return DashboardSettingsAuthority(AuthorityDocumentStore(
        "dashboard_settings",
        "DSS",
        "dashboard_settings",
        document_name="overlay",
        backend=backend,
    ))


@pytest.fixture
def shared_settings(tmp_path, monkeypatch):
    from maverick import audit, config, crypto_at_rest, governed_records
    from maverick.dashboard_settings_authority import (
        dashboard_settings_authority as real_authority,
    )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[security_ops]\nenable = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "postgres")
    monkeypatch.setenv("MAVERICK_PG_DSN", "postgresql://test.invalid/lightwork")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: SimpleNamespace(
            kind="postgres",
            dsn="postgresql://test.invalid/lightwork",
            shared_required=False,
        ),
    )
    monkeypatch.setattr(crypto_at_rest, "at_rest_enabled", lambda: True)
    events: list[dict] = []

    def _audit(_kind, **payload):
        events.append(payload)
        return True

    monkeypatch.setattr(audit, "record", _audit)
    config.reset_config_cache()

    selected = {"authority": None}

    def _selected_authority():
        value = selected["authority"]
        return value if value is not None else real_authority()

    monkeypatch.setattr(
        "maverick.dashboard_settings_authority.dashboard_settings_authority",
        _selected_authority,
    )
    yield {
        "config_path": config_path,
        "events": events,
        "selected": selected,
    }
    config.reset_config_cache()


def test_cross_instance_concurrent_updates_converge_without_clobbering(
    shared_settings,
):
    from maverick import config
    from maverick_dashboard import settings_store

    backend = _TenantMemoryBackend(race_first_reads=True)
    first = _authority(backend)
    second = _authority(backend)
    authorities = {
        "provider-writer": first,
        "toggle-writer": second,
    }
    shared_settings["selected"]["authority"] = first

    import maverick.dashboard_settings_authority as authority_module

    original = authority_module.dashboard_settings_authority

    def _by_thread():
        return authorities.get(threading.current_thread().name, first)

    authority_module.dashboard_settings_authority = _by_thread
    try:
        threads = [
            threading.Thread(
                name="provider-writer",
                target=settings_store.set_provider,
                args=("anthropic",),
                kwargs={"api_key": "sk-shared-provider"},  # pragma: allowlist secret
            ),
            threading.Thread(
                name="toggle-writer",
                target=settings_store.set_toggle,
                args=("features", "skills", False),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
    finally:
        authority_module.dashboard_settings_authority = original

    expected = {
        "providers": {
            "anthropic": {"api_key": "sk-shared-provider"},  # pragma: allowlist secret
        },
        "features": {"skills": False},
    }
    assert first.read() == expected
    assert second.read() == expected
    assert settings_store.load_overlay() == expected
    assert config.load_global_config()["features"]["skills"] is False
    actions = {event.get("action") for event in shared_settings["events"]}
    assert {"set_provider", "set_toggle"}.issubset(actions)
    assert not config.dashboard_overrides_path().exists()


def test_security_suite_cas_conflicts_and_runtime_reads_shared_revision(
    shared_settings, monkeypatch,
):
    from maverick import config
    from maverick_dashboard import security_api, settings_store

    backend = _TenantMemoryBackend()
    first = _authority(backend)
    second = _authority(backend)
    shared_settings["selected"]["authority"] = first
    monkeypatch.setattr(
        "maverick.audit.global_audit_log",
        lambda: (_ for _ in ()).throw(
            AssertionError("shared settings must use governed-record audit")
        ),
    )

    assert settings_store.security_suite_revision() == 1
    revision = settings_store.set_security_suite(
        security_ops=False,
        threat_hunt=True,
        env_hunt=True,
        response_execution=False,
        actor="test:admin",
        expected_revision=1,
    )
    assert revision == 2

    shared_settings["selected"]["authority"] = second
    with pytest.raises(settings_store.SecuritySuiteRevisionConflict):
        settings_store.set_security_suite(
            security_ops=True,
            threat_hunt=False,
            env_hunt=False,
            response_execution=False,
            actor="test:other-admin",
            expected_revision=1,
        )

    effective = config.load_global_config()
    assert effective["security_ops"]["enable"] is False
    assert effective["threat_hunt"]["enable"] is True
    assert settings_store.security_suite_revision() == 2
    monkeypatch.setattr(security_api, "require_global_permission", lambda *_a: None)
    response = asyncio.run(security_api.security_config(object()))
    assert response["security_ops"] is False
    assert response["threat_hunt"] is True
    assert response["revision"] == 2
    assert any(
        event.get("action") == "set_security_suite"
        for event in shared_settings["events"]
    )


def test_security_suite_optional_fields_use_each_cas_retry_snapshot(
    shared_settings,
):
    from maverick_dashboard import settings_store

    class _RetryingAuthority:
        def __init__(self) -> None:
            self.document = {
                "evidence_graph": {"enable": False},
                "security_suite_control": {"revision": 1},
            }

        def read(self):
            return copy.deepcopy(self.document)

        def mutate(self, callback, **_kwargs):
            stale = copy.deepcopy(self.document)
            callback(stale)
            # A different settings writer wins without touching the suite CAS
            # revision. AuthorityDocumentStore then replays the callback.
            self.document["evidence_graph"]["enable"] = True
            current = copy.deepcopy(self.document)
            result = callback(current)
            self.document = current
            return result

    authority = _RetryingAuthority()
    shared_settings["selected"]["authority"] = authority
    assert settings_store.set_security_suite(
        security_ops=False,
        evidence_graph=None,
        threat_hunt=False,
        env_hunt=False,
        response_execution=False,
        actor="test:admin",
        expected_revision=1,
    ) == 2
    assert authority.document["evidence_graph"]["enable"] is True


def test_shared_settings_are_tenant_scoped(shared_settings, monkeypatch):
    from maverick_dashboard import settings_store

    backend = _TenantMemoryBackend()
    authority = _authority(backend)
    shared_settings["selected"]["authority"] = authority

    settings_store.set_provider(
        "openai",
        api_key="sk-alpha",  # pragma: allowlist secret
    )
    monkeypatch.setenv("MAVERICK_TENANT", "beta")
    assert settings_store.load_overlay() == {}
    settings_store.set_toggle("features", "streaming", False)

    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    alpha = settings_store.load_overlay()
    assert alpha["providers"]["openai"]["api_key"] == "sk-alpha"  # pragma: allowlist secret
    assert "features" not in alpha
    monkeypatch.setenv("MAVERICK_TENANT", "beta")
    assert settings_store.load_overlay() == {
        "features": {"streaming": False},
    }


def test_shared_failure_never_reads_or_writes_local_overlay(
    shared_settings, monkeypatch,
):
    from maverick import config
    from maverick_dashboard import security_api, settings_store

    class _Unavailable:
        def read(self):
            raise RuntimeError("database unavailable")

        def mutate(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    shared_settings["selected"]["authority"] = _Unavailable()
    with pytest.raises(settings_store.SecuritySuiteConfigUnavailable):
        settings_store.load_overlay()
    with pytest.raises(settings_store.SecuritySuiteConfigUnavailable):
        settings_store.set_toggle("features", "skills", False)
    with pytest.raises(RuntimeError, match="database unavailable"):
        config.load_global_config()
    monkeypatch.setattr(security_api, "require_global_permission", lambda *_a: None)
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(security_api.security_config(object()))
    assert not config.dashboard_overrides_path().exists()


@pytest.mark.parametrize("operation", ["read", "mutate"])
def test_shared_settings_detect_backend_drift_after_operation(
    shared_settings,
    operation,
):
    from maverick.dashboard_settings_authority import (
        DashboardSettingsAuthorityError,
    )

    class _DriftingStore:
        backend_kind = "postgres"

        def read(self):
            self.backend_kind = "local"
            return {}

        def mutate(self, callback, **_kwargs):
            result = callback({})
            self.backend_kind = "local"
            return result

    authority = DashboardSettingsAuthority(_DriftingStore())
    with pytest.raises(DashboardSettingsAuthorityError, match="Postgres"):
        if operation == "read":
            authority.read()
        else:
            authority.mutate(lambda _document: None, action="test", actor="test")


def test_postgres_cutover_refuses_unmigrated_local_overlay(
    shared_settings,
):
    from maverick import config
    from maverick.dashboard_settings_authority import (
        DashboardSettingsAuthorityError,
    )
    from maverick_dashboard import settings_store

    backend = _TenantMemoryBackend()
    shared_settings["selected"]["authority"] = _authority(backend)
    local = config.dashboard_overrides_path()
    local.write_text(
        "[features]\nskills = true\n",
        encoding="utf-8",
    )
    before = local.read_bytes()

    with pytest.raises(
        DashboardSettingsAuthorityError,
        match="migrate.*archive",
    ):
        config.load_global_config()
    with pytest.raises(settings_store.SecuritySuiteConfigUnavailable):
        settings_store.set_toggle("features", "skills", False)
    assert local.read_bytes() == before
    assert backend.records == {}


@pytest.mark.parametrize(
    "document",
    [
        {"unknown_section": {}},
        {"value": {"hourly_rate": float("nan")}},
        {"providers": {"openai": {"api_key": "x" * (64 * 1024 + 1)}}},
        {
            "env_hunt": {
                "a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {}}}}}}}}
            },
        },
    ],
)
def test_shared_schema_rejects_pathological_documents(
    shared_settings,
    document,
):
    from maverick.dashboard_settings_authority import (
        DashboardSettingsAuthorityError,
    )

    class _DocumentStore:
        backend_kind = "postgres"

        def read(self):
            return copy.deepcopy(document)

        def mutate(self, callback, **_kwargs):
            value = copy.deepcopy(document)
            return callback(value)

    authority = DashboardSettingsAuthority(_DocumentStore())
    with pytest.raises(DashboardSettingsAuthorityError):
        authority.read()


def test_shared_schema_validates_the_post_mutation_document(shared_settings):
    from maverick.dashboard_settings_authority import (
        DashboardSettingsAuthorityError,
    )

    class _DocumentStore:
        backend_kind = "postgres"

        def read(self):
            return {}

        def mutate(self, callback, **_kwargs):
            return callback({})

    authority = DashboardSettingsAuthority(_DocumentStore())

    def _invalid(document):
        document["not_dashboard_policy"] = {}

    with pytest.raises(DashboardSettingsAuthorityError):
        authority.mutate(_invalid, action="invalid", actor="test")


def test_shared_settings_require_an_active_tenant(shared_settings, monkeypatch):
    from maverick_dashboard import settings_store

    backend = _TenantMemoryBackend()
    shared_settings["selected"]["authority"] = _authority(backend)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    with pytest.raises(
        settings_store.SecuritySuiteConfigUnavailable,
        match="shared dashboard settings",
    ):
        settings_store.load_overlay()
