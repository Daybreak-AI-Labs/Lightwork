"""The shared knowledge factory is the only runtime construction boundary."""
from __future__ import annotations

import hashlib
import sys
import types
from types import SimpleNamespace

import pytest


def _install_factory_doubles(
    monkeypatch, tmp_path, *, ocr_error=None, provision_key=True,
):
    import maverick.config as config
    import maverick.production_exclusions as exclusions
    from maverick.file_lock import atomic_write_bytes, prepare_custody_directory

    monkeypatch.setattr(exclusions, "feature_allowed", lambda _name: True)
    key_dir = prepare_custody_directory(tmp_path / "knowledge-key-custody")
    key_path = key_dir / "active.key"
    if provision_key:
        atomic_write_bytes(key_path, b"k" * 32)
    monkeypatch.setattr(
        config,
        "get_knowledge",
        lambda: {
            "enable": True,
            "path": str(tmp_path / "knowledge.db"),
            "store": "sqlite",
            "embedder": "deterministic",
            "integrity_key_file": str(key_path),
            "integrity_previous_key_files": [],
            "quarantine_head_file": str(
                tmp_path / "knowledge-head-custody" / "head.json"
            ),
        },
    )
    captured = {}

    class _KB:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _Ledger:
        def __init__(self, path, **kwargs):
            self.path = path
            self.kwargs = kwargs

        def record(self, **_kwargs):
            return None

        def close(self):
            return None

    class _Store:
        backend_name = "sqlite"

        def close(self):
            return None

    class _Embedder:
        provider_name = "deterministic"

    class _DeterministicEmbedder:
        pass

    knowledge = types.ModuleType("maverick_knowledge")
    knowledge.DeterministicEmbedder = _DeterministicEmbedder
    knowledge.KnowledgeBase = _KB
    knowledge.SqliteQuarantineLedger = _Ledger
    knowledge.build_store = lambda _cfg: _Store()
    knowledge.build_embedder = lambda _cfg: _Embedder()
    image = types.ModuleType("maverick_knowledge.image")
    if ocr_error is None:
        image.build_ocr_describer = lambda: (lambda _path: "ocr")
    else:
        image.build_ocr_describer = lambda: (_ for _ in ()).throw(ocr_error)
    shield = types.ModuleType("maverick_shield")
    shield.Shield = SimpleNamespace(
        from_config=lambda **_kwargs: SimpleNamespace(
            backend="builtin", profile="strict", scan_output=lambda _text: None,
        )
    )
    monkeypatch.setitem(sys.modules, "maverick_knowledge", knowledge)
    monkeypatch.setitem(sys.modules, "maverick_knowledge.image", image)
    monkeypatch.setitem(sys.modules, "maverick_shield", shield)
    return captured


def test_required_factory_propagates_all_ingestion_controls(
    monkeypatch, tmp_path,
):
    captured = _install_factory_doubles(monkeypatch, tmp_path)
    from maverick.knowledge_factory import open_configured_knowledge_base

    kb = open_configured_knowledge_base(required_mode=True)
    assert kb is not None
    assert captured["required_mode"] is True
    assert callable(captured["shield"].scan_output)
    assert callable(captured["image_describer"])
    assert callable(captured["quarantine"].record)
    assert callable(captured["principal_resolver"])
    assert captured["integrity_key"] == b"k" * 32
    assert captured["active_integrity_key_id"]
    assert captured["integrity_keys"] == {
        captured["active_integrity_key_id"]: b"k" * 32,
    }
    assert captured["policy_id"] == "knowledge-ingest-v1:strict:builtin"
    assert str(captured["quarantine"].path).endswith(
        "knowledge.db.quarantine.sqlite3"
    )


def test_required_factory_never_substitutes_os_user_for_missing_authentication(
    monkeypatch, tmp_path,
):
    captured = _install_factory_doubles(monkeypatch, tmp_path)
    import maverick.connections as connections
    from maverick.knowledge_factory import open_configured_knowledge_base

    monkeypatch.setattr(connections, "current_principal", lambda: None)
    assert open_configured_knowledge_base(required_mode=True) is not None
    assert captured["principal_resolver"]() == ""


def test_required_factory_refuses_missing_describer(monkeypatch, tmp_path):
    _install_factory_doubles(
        monkeypatch, tmp_path, ocr_error=RuntimeError("vision extra unavailable"),
    )
    from maverick.knowledge_factory import (
        KnowledgeFactoryError,
        open_configured_knowledge_base,
    )

    with pytest.raises(KnowledgeFactoryError, match="image/OCR describer"):
        open_configured_knowledge_base(required_mode=True)


def test_required_factory_refuses_missing_integrity_key(monkeypatch, tmp_path):
    _install_factory_doubles(monkeypatch, tmp_path, provision_key=False)
    from maverick.knowledge_factory import (
        KnowledgeFactoryError,
        open_configured_knowledge_base,
    )

    with pytest.raises(KnowledgeFactoryError, match="pre-provisioned integrity key"):
        open_configured_knowledge_base(required_mode=True)


def test_runtime_and_admin_constructors_delegate_to_factory(monkeypatch):
    import maverick.knowledge_admin as admin
    import maverick.knowledge_factory as factory
    import maverick.orchestrator as orchestrator

    sentinel = object()
    calls = []

    def _open(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(factory, "open_configured_knowledge_base", _open)
    assert orchestrator._build_knowledge() is sentinel
    assert admin.open_knowledge_base(tenant="tenant-a") is sentinel
    assert calls == [{}, {"tenant": "tenant-a"}]


def test_explicit_tenant_scopes_factory_paths_and_restores_context(
    monkeypatch, tmp_path,
):
    import maverick.knowledge_factory as factory
    from maverick.paths import current_tenant_id
    from maverick.workspace import Workspace

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    observed = []
    before = current_tenant_id()

    def _open(*, required_mode):
        observed.append(
            (required_mode, current_tenant_id(), Workspace.current().knowledge_path)
        )
        return object()

    monkeypatch.setattr(factory, "_open_current_knowledge_base", _open)
    assert factory.open_configured_knowledge_base(
        tenant="tenant-a", required_mode=True,
    ) is not None
    assert observed == [(
        True,
        "tenant-a",
        tmp_path / "tenants" / "tenant-a" / "knowledge.db",
    )]
    assert current_tenant_id() == before


def test_required_factory_rejects_colocated_or_implicit_key_custody(
    monkeypatch, tmp_path,
):
    _install_factory_doubles(monkeypatch, tmp_path)
    import maverick.config as config
    from maverick.knowledge_factory import (
        KnowledgeFactoryError,
        open_configured_knowledge_base,
    )

    base = {
        "enable": True,
        "path": str(tmp_path / "knowledge.db"),
        "store": "sqlite",
        "embedder": "deterministic",
        "integrity_previous_key_files": [],
    }
    monkeypatch.setattr(config, "get_knowledge", lambda: dict(base))
    with pytest.raises(KnowledgeFactoryError, match="external custody"):
        open_configured_knowledge_base(required_mode=True)

    colocated = tmp_path / "knowledge.key"
    colocated.write_bytes(b"c" * 32)
    monkeypatch.setattr(
        config,
        "get_knowledge",
        lambda: {**base, "integrity_key_file": str(colocated)},
    )
    with pytest.raises(KnowledgeFactoryError, match="must not be colocated"):
        open_configured_knowledge_base(required_mode=True)


def test_required_factory_loads_previous_keys_for_rotation(monkeypatch, tmp_path):
    captured = _install_factory_doubles(monkeypatch, tmp_path)
    import maverick.config as config
    from maverick.file_lock import atomic_write_bytes
    from maverick.knowledge_factory import open_configured_knowledge_base

    key_dir = tmp_path / "knowledge-key-custody"
    active_path = key_dir / "active.key"
    previous_path = key_dir / "previous.key"
    previous_key = b"p" * 32
    atomic_write_bytes(previous_path, previous_key)
    monkeypatch.setattr(
        config,
        "get_knowledge",
        lambda: {
            "enable": True,
            "path": str(tmp_path / "knowledge.db"),
            "store": "sqlite",
            "embedder": "deterministic",
            "integrity_key_file": str(active_path),
            "integrity_previous_key_files": [str(previous_path)],
            "quarantine_head_file": str(
                tmp_path / "knowledge-head-custody" / "head.json"
            ),
        },
    )

    assert open_configured_knowledge_base(required_mode=True) is not None
    previous_id = hashlib.sha256(previous_key).hexdigest()[:16]
    active_id = hashlib.sha256(b"k" * 32).hexdigest()[:16]
    assert captured["active_integrity_key_id"] == active_id
    assert captured["integrity_keys"] == {
        active_id: b"k" * 32,
        previous_id: previous_key,
    }


@pytest.mark.parametrize(
    "config",
    [
        {"embedder": "hosted", "base_url": "http://embed.internal"},
        {
            "embedder": "deterministic",
            "store": "qdrant",
            "url": "http://qdrant.internal",
        },
        {
            "embedder": "deterministic",
            "store": "pgvector",
            "dsn": "postgresql://db/knowledge?sslmode=disable",
        },
        {
            "embedder": "deterministic",
            "store": "pgvector",
            "dsn": "postgresql://db/knowledge",
        },
    ],
)
def test_required_factory_refuses_plaintext_knowledge_transports(config):
    from maverick.knowledge_factory import (
        KnowledgeFactoryError,
        _validate_required_transports,
    )

    with pytest.raises(KnowledgeFactoryError):
        _validate_required_transports(config)


def test_required_factory_accepts_authenticated_encrypted_transports():
    from maverick.knowledge_factory import _validate_required_transports

    _validate_required_transports(
        {"embedder": "hosted", "base_url": "https://embed.example/v1"}
    )
    _validate_required_transports(
        {
            "embedder": "deterministic",
            "store": "qdrant",
            "url": "https://qdrant.example",
        }
    )
    _validate_required_transports(
        {
            "embedder": "deterministic",
            "store": "pgvector",
            "dsn": "postgresql://db/knowledge?sslmode=verify-full",
        }
    )


def test_knowledge_config_uses_effective_provider_defaults(monkeypatch):
    import maverick.config as config

    monkeypatch.delenv("MAVERICK_EMBED_PROVIDER", raising=False)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"knowledge": {"embedder": "cohere"}},
    )
    cohere = config.get_knowledge()
    assert cohere["model"] == "embed-v4.0"
    assert cohere["base_url"] == "https://api.cohere.com/v2"

    monkeypatch.setenv("MAVERICK_EMBED_PROVIDER", "local")
    local = config.get_knowledge()
    assert local["model"] == "all-MiniLM-L6-v2"
