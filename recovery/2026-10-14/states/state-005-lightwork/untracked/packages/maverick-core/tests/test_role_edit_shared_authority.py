"""Shared authority contract for tenant role/model policy overrides."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import agent, config, effort, llm, role_edit, routing


class _MemoryDocument:
    def __init__(self, document=None, *, backends=None) -> None:
        self.document = copy.deepcopy(document or {})
        self.lock = threading.Lock()
        self.backends = list(backends or ["postgres"])
        self.backend_reads = 0
        self.mutations: list[dict] = []

    @property
    def backend_kind(self) -> str:
        index = min(self.backend_reads, len(self.backends) - 1)
        self.backend_reads += 1
        return self.backends[index]

    def read(self):
        with self.lock:
            return copy.deepcopy(self.document)

    def mutate(self, callback, **kwargs):
        with self.lock:
            candidate = copy.deepcopy(self.document)
            result = callback(candidate)
            self.document = candidate
            self.mutations.append(dict(kwargs))
            return result


@pytest.fixture
def shared(monkeypatch, tmp_path):
    path = tmp_path / "roles.toml"
    store = _MemoryDocument()
    monkeypatch.delenv("MAVERICK_ROLES_FILE", raising=False)
    monkeypatch.setattr(role_edit, "roles_file", lambda: path)
    monkeypatch.setattr(role_edit, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(role_edit, "_require_active_tenant", lambda: None)
    monkeypatch.setattr(role_edit, "_shared_authority", lambda: store)
    return path, store


def test_shared_role_policy_round_trips_without_local_write(shared):
    path, store = shared

    location = role_edit.write_role_override(
        "coder",
        {
            "system_addendum": "Prefer verified, surgical changes.",
            "model": "tenant:coder",
            "effort": "high",
        },
    )

    assert location == "governed:role_overrides"
    assert role_edit.role_addendum("coder") == "Prefer verified, surgical changes."
    assert role_edit.override_model("coder") == "tenant:coder"
    assert role_edit.override_effort("coder") == "high"
    assert not path.exists()
    assert store.mutations == [{
        "action": "role_override.write",
        "actor": "role-editor",
    }]
    assert role_edit.remove_role_override("coder") is True
    assert role_edit.remove_role_override("coder") is False
    assert role_edit.override_model("coder") is None


def test_shared_concurrent_writers_accumulate_distinct_roles(shared):
    _path, _store = shared
    roles = ["coder", "writer", "analyst", "researcher", "revisor"]

    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        list(pool.map(
            lambda role: role_edit.write_role_override(
                role,
                {"system_addendum": f"Policy for {role}."},
            ),
            roles,
        ))

    assert {
        role: role_edit.role_addendum(role)
        for role in roles
    } == {
        role: f"Policy for {role}."
        for role in roles
    }


def test_shared_read_outage_does_not_fall_back_to_local(monkeypatch, shared):
    path, _store = shared
    path.write_text('[coder]\nmodel = "local:bypass"\n', encoding="utf-8")

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="migrate"):
        role_edit.override_model("coder")


def test_shared_backend_failure_does_not_create_local_state(monkeypatch, shared):
    path, _store = shared

    class _Unavailable:
        @property
        def backend_kind(self):
            raise OSError("database unavailable")

    monkeypatch.setattr(role_edit, "_shared_authority", lambda: _Unavailable())
    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="unavailable"):
        role_edit.write_role_override("coder", {"model": "tenant:coder"})
    assert not path.exists()


def test_backend_drift_after_read_is_rejected(monkeypatch, shared):
    _path, _store = shared
    store = _MemoryDocument(backends=["postgres", "local"])
    monkeypatch.setattr(role_edit, "_shared_authority", lambda: store)

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="require governed"):
        role_edit.list_roles()


def test_authority_loss_during_effective_resolution_is_not_masked(
    monkeypatch,
    shared,
):
    _path, _store = shared

    class _FailAfterFirstRead(_MemoryDocument):
        def read(self):
            if self.backend_reads >= 2:
                raise OSError("database unavailable")
            return super().read()

    store = _FailAfterFirstRead({
        "roles": {"coder": {"model": "tenant:coder"}},
        "schema_version": 1,
    })
    monkeypatch.setattr(role_edit, "_shared_authority", lambda: store)

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="could not be read"):
        role_edit.resolved_role("coder")


def test_malformed_shared_document_is_not_treated_as_empty(monkeypatch, shared):
    _path, _store = shared
    store = _MemoryDocument({
        "roles": {"coder": {"model": "tenant:coder", "unknown": "x"}},
        "schema_version": 1,
    })
    monkeypatch.setattr(role_edit, "_shared_authority", lambda: store)

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="unknown field"):
        role_edit.override_model("coder")
    before = copy.deepcopy(store.document)
    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="unknown field"):
        role_edit.write_role_override("writer", {"effort": "high"})
    assert store.document == before


def test_shared_mode_refuses_explicit_local_path(shared, tmp_path):
    _path, store = shared

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="local role-file"):
        role_edit.write_role_override(
            "coder",
            {"model": "tenant:coder"},
            path=tmp_path / "alternate.toml",
        )
    assert store.document == {}


def test_shared_mode_requires_active_tenant(monkeypatch, tmp_path):
    monkeypatch.delenv("MAVERICK_ROLES_FILE", raising=False)
    monkeypatch.setattr(role_edit, "roles_file", lambda: tmp_path / "roles.toml")
    monkeypatch.setattr(role_edit, "_configured_backend", lambda: "postgres")
    from maverick import paths
    monkeypatch.setattr(paths, "current_tenant_id_strict", lambda: None)

    with pytest.raises(role_edit.RoleOverrideAuthorityError, match="active tenant"):
        role_edit.role_addendum("coder")


def test_authority_outage_is_mandatory_at_all_execution_consumers(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise role_edit.RoleOverrideAuthorityError("database unavailable")

    monkeypatch.setattr(role_edit, "override_model", unavailable)
    monkeypatch.setattr(role_edit, "override_effort", unavailable)
    monkeypatch.setattr(role_edit, "role_addendum", unavailable)
    for name in (
        "MAVERICK_EFFORT",
        "MAVERICK_EFFORT_CODER",
        "MAVERICK_EFFORT_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(role_edit.RoleOverrideAuthorityError):
        config.get_role_model("coder")
    with pytest.raises(role_edit.RoleOverrideAuthorityError):
        llm.model_for_role("coder")
    with pytest.raises(role_edit.RoleOverrideAuthorityError):
        routing.pick(routing.RouteSignal(role="coder"))
    with pytest.raises(role_edit.RoleOverrideAuthorityError):
        effort.effort_for_role("coder", "tenant:coder")
    with pytest.raises(role_edit.RoleOverrideAuthorityError):
        agent.apply_role_overlays(
            "base prompt",
            role="coder",
            domain_persona=None,
        )
