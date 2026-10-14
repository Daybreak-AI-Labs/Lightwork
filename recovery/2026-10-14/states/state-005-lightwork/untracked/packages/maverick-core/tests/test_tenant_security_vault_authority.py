"""Hostile/concurrent contracts for shared tenant key and vault authority."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

pytest.importorskip("cryptography")

from maverick import browser_auth_vault as browser  # noqa: E402
from maverick import config, governed_records, oauth_vault, paths  # noqa: E402
from maverick.connections import bind_principal  # noqa: E402
from maverick.crypto_at_rest import EncryptionUnavailable  # noqa: E402
from maverick.tenant import kms, security_authority  # noqa: E402


class _MemoryPlane:
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.documents: dict[object, dict] = {}
        self.locks: dict[object, threading.RLock] = {}
        self.backend = "postgres"
        self.fail_after_commit_action: str | None = None
        self.retry_once_action: str | None = None
        self.retry_advance = 0.0

    def lock(self, key: object) -> threading.RLock:
        return self.locks.setdefault(key, threading.RLock())

    def store(self, key: object, tenant_id: str):
        return _MemoryStore(self, key, tenant_id)


class _MemoryStore:
    def __init__(self, plane: _MemoryPlane, key: object, tenant_id: str) -> None:
        self.plane = plane
        self.key = key
        self.tenant_id = tenant_id

    @property
    def backend_kind(self) -> str:
        return self.plane.backend

    def _assert_scope(self) -> None:
        assert paths.current_tenant_id_strict() == self.tenant_id

    def read(self):
        self._assert_scope()
        with self.plane.lock(self.key):
            return copy.deepcopy(self.plane.documents.get(self.key, {}))

    def authoritative_time(self) -> float:
        self._assert_scope()
        return self.plane.clock[0]

    def mutate_with_authoritative_time(self, callback, *, action: str, **_kwargs):
        self._assert_scope()
        with self.plane.lock(self.key):
            current = copy.deepcopy(self.plane.documents.get(self.key, {}))
            if self.plane.retry_once_action == action:
                callback(copy.deepcopy(current), self.plane.clock[0])
                self.plane.retry_once_action = None
                self.plane.clock[0] += self.plane.retry_advance
            candidate = copy.deepcopy(current)
            result = callback(candidate, self.plane.clock[0])
            self.plane.documents[self.key] = candidate
            if self.plane.fail_after_commit_action == action:
                self.plane.fail_after_commit_action = None
                raise OSError("response lost after durable commit")
            return result


@contextmanager
def _tenant(tenant_id: str):
    token = paths.set_tenant(tenant_id)
    try:
        yield
    finally:
        paths.reset_tenant(token)


@pytest.fixture
def shared(monkeypatch, tmp_path):
    clock = [2_000_000_000.0]
    policy = _MemoryPlane(clock)
    deks = _MemoryPlane(clock)
    oauth = _MemoryPlane(clock)
    browser_plane = _MemoryPlane(clock)

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "11" * 32)
    monkeypatch.setenv("MAVERICK_KMS_KEK", "22" * 32)
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "1")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "postgres")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_VAULT_KEY", raising=False)
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(
        security_authority,
        "shared_security_authority_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        security_authority,
        "_new_policy_store",
        lambda tenant_id: policy.store(tenant_id, tenant_id),
    )
    monkeypatch.setattr(
        security_authority,
        "_new_wrapped_dek_store",
        lambda tenant_id: deks.store(tenant_id, tenant_id),
    )
    monkeypatch.setattr(
        oauth_vault,
        "_new_shared_store",
        lambda tenant_id: oauth.store(tenant_id, tenant_id),
    )
    monkeypatch.setattr(
        browser,
        "_new_shared_store",
        lambda tenant_id, principal: browser_plane.store(
            (tenant_id, principal),
            tenant_id,
        ),
    )
    config.reset_config_cache()
    kms._clear_cache()
    yield {
        "clock": clock,
        "policy": policy,
        "deks": deks,
        "oauth": oauth,
        "browser": browser_plane,
        "home": tmp_path,
    }
    kms._clear_cache()
    config.reset_config_cache()


def _policy_path(home, tenant_id: str):
    return home / "tenants" / paths._tenant_segment(tenant_id) / "config.toml"


def test_policy_legacy_requires_explicit_secret_safe_import(shared, monkeypatch):
    path = _policy_path(shared["home"], "acme")
    path.parent.mkdir(parents=True)
    path.write_text(
        '[models]\nplanner = "openai:gpt"\n'
        '[providers.openai]\napi_key = "literal-secret"\n',  # pragma: allowlist secret
        encoding="utf-8",
    )

    with pytest.raises(
        security_authority.TenantSecurityAuthorityError,
        match="explicitly import",
    ):
        security_authority.load_policy_overlay("acme")
    with pytest.raises(
        security_authority.TenantSecurityAuthorityError,
        match="external secret reference",
    ):
        security_authority.import_legacy_policy_overlay("acme")

    path.write_text(
        '[models]\nplanner = "openai:gpt"\n'
        '[providers.openai]\napi_key = "${ACME_OPENAI_KEY}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ACME_OPENAI_KEY", "runtime-only-secret")
    imported = security_authority.import_legacy_policy_overlay("acme")
    assert imported.startswith("sha256:")
    overlay = security_authority.load_policy_overlay("acme")
    assert overlay["providers"]["openai"]["api_key"] == "${ACME_OPENAI_KEY}"

    with _tenant("acme"), config.without_shared_dashboard_settings():
        effective = config.load_config()
    assert effective["providers"]["openai"]["api_key"] == "runtime-only-secret"  # pragma: allowlist secret
    persisted = json.dumps(shared["policy"].documents["acme"], sort_keys=True)
    assert "runtime-only-secret" not in persisted
    assert "literal-secret" not in persisted

    path.write_text('[models]\nplanner = "changed"\n', encoding="utf-8")
    with pytest.raises(
        security_authority.TenantSecurityAuthorityError,
        match="explicitly import",
    ):
        security_authority.load_policy_overlay("acme")


@pytest.mark.parametrize(
    "key",
    [
        "dsn",
        "database_url",
        "connection_string",
        "knowledge_dsn",
    ],
)
def test_policy_requires_external_reference_for_credential_dsn(shared, key):
    with pytest.raises(
        security_authority.TenantSecurityAuthorityError,
        match="external secret reference",
    ):
        security_authority.set_policy_overlay(
            "acme",
            {"postgres": {key: "postgres://user:pass@db.example/acme"}},  # pragma: allowlist secret
        )

    env_reference = "${MAVERICK_PG_DSN}"
    security_authority.set_policy_overlay(
        "acme",
        {"postgres": {key: env_reference}},
    )
    assert security_authority.load_policy_overlay("acme")["postgres"][key] == (
        env_reference
    )

    secret_reference = "secret://tenants/acme/postgres-dsn"  # pragma: allowlist secret
    security_authority.set_policy_overlay(
        "acme",
        {"postgres": {key: secret_reference}},
    )
    assert security_authority.load_policy_overlay("acme")["postgres"][key] == (
        secret_reference
    )


class _BarrierKMS:
    def __init__(self, key: bytes, barrier: threading.Barrier) -> None:
        self.inner = kms.LocalKMS(key)
        self.barrier = barrier

    def wrap(self, dek: bytes, *, context: bytes | None = None) -> bytes:
        self.barrier.wait(timeout=10)
        return self.inner.wrap(dek, context=context)

    def unwrap(self, wrapped: bytes, *, context: bytes | None = None) -> bytes:
        return self.inner.unwrap(wrapped, context=context)


def test_concurrent_first_dek_creation_converges_and_is_not_plaintext(shared):
    barrier = threading.Barrier(2)
    provider = _BarrierKMS(b"\x44" * 32, barrier)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _index: kms.tenant_dek("acme", kms=provider),
            range(2),
        ))

    assert results[0] == results[1]
    document = shared["deks"].documents["acme"]
    assert document["wrapped_dek"]["generation"] == 1
    persisted = json.dumps(document, sort_keys=True).encode()
    assert results[0] not in persisted
    assert document["tenant_id"] == "acme"


def test_lost_create_response_retries_without_second_dek(shared):
    shared["deks"].fail_after_commit_action = "tenant_dek.create"
    provider = kms.LocalKMS(b"\x45" * 32)
    with pytest.raises(EncryptionUnavailable):
        kms.tenant_dek("acme", kms=provider)

    first_recovery = kms.tenant_dek("acme", kms=provider)
    kms._clear_cache()
    second_recovery = kms.tenant_dek("acme", kms=provider)
    assert first_recovery == second_recovery
    assert shared["deks"].documents["acme"]["wrapped_dek"]["generation"] == 1


def test_kms_policy_drift_is_digest_bound_and_fails_closed(shared):
    security_authority.set_policy_overlay(
        "acme",
        {"kms": {"provider": "aws", "key_id": "cmk-a"}},
    )
    provider = kms.LocalKMS(b"\x46" * 32)
    kms.tenant_dek("acme", kms=provider)
    old_digest = shared["policy"].documents["acme"]["content_digest"]
    security_authority.set_policy_overlay(
        "acme",
        {"kms": {"provider": "aws", "key_id": "cmk-b"}},
        expected_digest=old_digest,
    )
    kms._clear_cache()

    with pytest.raises(EncryptionUnavailable, match="policy changed"):
        kms.tenant_dek("acme", kms=provider)


def test_concurrent_kek_rotation_has_one_winner_and_one_adopter(shared):
    old = kms.LocalKMS(b"\x47" * 32)
    kms.tenant_dek("acme", kms=old)
    kms._clear_cache()
    barrier = threading.Barrier(2)
    new = _BarrierKMS(b"\x48" * 32, barrier)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda _index: kms.rotate_kek_idempotent(
                "acme",
                old_kms=old,
                new_kms=new,
            ),
            range(2),
        ))

    assert sorted(outcomes) == ["rotated", "skipped"]
    assert shared["deks"].documents["acme"]["wrapped_dek"]["generation"] == 2
    kms._clear_cache()
    assert len(kms.tenant_dek("acme", kms=new)) == 32


def test_wrapped_dek_legacy_blocks_then_imports_exact_ciphertext(shared):
    tenant_id = "legacy"
    raw_dek = b"\x49" * 32
    provider = kms.LocalKMS(b"\x4a" * 32)
    context = kms._tenant_context(tenant_id, b"dek-wrap")
    wrapped = provider.wrap(raw_dek, context=context)
    path = security_authority._legacy_wrapped_dek_path(tenant_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(wrapped)

    with pytest.raises(
        security_authority.TenantSecurityAuthorityError,
        match="explicitly import",
    ):
        security_authority.read_wrapped_dek(tenant_id)
    policy_digest = kms._kms_policy_digest(kms._kms_config_for(tenant_id))
    security_authority.import_legacy_wrapped_dek(
        tenant_id,
        policy_digest=policy_digest,
    )
    assert kms.tenant_dek(tenant_id, kms=provider) == raw_dek


def test_tenant_scoped_ciphertext_rejects_cross_tenant_and_scope(shared):
    blob = kms.seal_scoped_for_tenant("acme", b"credential", scope=b"provider:a")
    with pytest.raises(EncryptionUnavailable):
        kms.unseal_scoped_for_tenant("beta", blob, scope=b"provider:a")
    with pytest.raises(EncryptionUnavailable):
        kms.unseal_scoped_for_tenant("acme", blob, scope=b"provider:b")


def test_bootstrap_scope_uses_deployment_key_not_recursive_tenant_key(shared):
    from maverick import crypto_at_rest

    with crypto_at_rest.deployment_key_scope():
        blob = crypto_at_rest.seal(b"bootstrap")
    assert blob.startswith((b"MVKAR1\n", b"MVKAR2\n"))


def test_oauth_cross_tenant_isolation_and_no_plaintext_in_authority(shared):
    acme = oauth_vault.OAuthVault("acme")
    beta = oauth_vault.OAuthVault("beta")
    acme.put("github", {"access_token": "acme-secret", "expires_in": 3600})
    assert beta.get("github") is None
    assert acme.get("github")["access_token"] == "acme-secret"
    persisted = json.dumps(shared["oauth"].documents["acme"], sort_keys=True)
    assert "acme-secret" not in persisted


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("access_token", 123),
        ("refresh_token", False),
        ("expires_at", "oops"),
        ("expires_at", True),
        ("expires_at", -1),
        ("expires_at", float("nan")),
        ("expires_at", float("inf")),
        ("obtained_at", "oops"),
        ("obtained_at", True),
        ("obtained_at", -1),
        ("obtained_at", float("nan")),
        ("obtained_at", float("inf")),
        ("expires_in", "3600"),
        ("expires_in", False),
        ("expires_in", -1),
        ("expires_in", float("nan")),
        ("expires_in", float("inf")),
    ],
)
def test_oauth_shared_rejects_malformed_security_fields(shared, field, value):
    vault = oauth_vault.OAuthVault("acme")
    record = {"access_token": "token", field: value}
    with pytest.raises(oauth_vault.OAuthVaultAuthorityError):
        vault.put("github", record)
    assert vault.get("github") is None


def test_oauth_shared_accepts_zero_numeric_expiry_as_expired(shared):
    vault = oauth_vault.OAuthVault("acme")
    vault.put(
        "github",
        {
            "access_token": "token",
            "obtained_at": 0,
            "expires_in": 0,
        },
    )
    assert vault.status("github")["expired"] is True


def test_oauth_put_lost_response_is_content_idempotent(shared):
    vault = oauth_vault.OAuthVault("acme")
    shared["oauth"].fail_after_commit_action = "oauth_vault.put"
    record = {"access_token": "secret", "expires_in": 3600}
    with pytest.raises(OSError, match="response lost"):
        vault.put("github", record)
    vault.put("github", record)
    assert shared["oauth"].documents["acme"]["providers"]["github"][
        "generation"
    ] == 1


def test_oauth_refresh_claim_prevents_duplicate_provider_effect(shared):
    vault = oauth_vault.OAuthVault("acme")
    vault.put(
        "github",
        {
            "access_token": "stale",
            "refresh_token": "one-time",
            "expires_at": shared["clock"][0] - 1,
        },
    )
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def refresher(record):
        calls.append(record["refresh_token"])
        entered.set()
        assert release.wait(timeout=10)
        return {"access_token": "fresh", "expires_in": 3600}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(vault.access_token, "github", refresher=refresher)
        assert entered.wait(timeout=10)
        second = pool.submit(vault.access_token, "github", refresher=refresher)
        assert second.result(timeout=10) is None
        release.set()
        assert first.result(timeout=10) == "fresh"

    assert calls == ["one-time"]
    entry = shared["oauth"].documents["acme"]["providers"]["github"]
    assert entry["generation"] == 2
    assert entry["refresh"] is None


def test_oauth_ambiguous_refresh_is_never_automatically_replayed(shared):
    vault = oauth_vault.OAuthVault("acme")
    vault.put(
        "github",
        {
            "access_token": "stale",
            "refresh_token": "one-time",
            "expires_at": shared["clock"][0] - 1,
        },
    )
    calls = 0

    def uncertain(_record):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider outcome unknown")

    with pytest.raises(TimeoutError):
        vault.access_token("github", refresher=uncertain)
    shared["clock"][0] += 10_000
    assert vault.access_token("github", refresher=uncertain) is None
    assert calls == 1
    entry = shared["oauth"].documents["acme"]["providers"]["github"]
    assert entry["refresh"]["phase"] == "ambiguous"


def test_oauth_refresh_retry_reclocks_claim_after_lost_cas(shared):
    vault = oauth_vault.OAuthVault("acme")
    vault.put(
        "github",
        {"access_token": "stale", "expires_at": shared["clock"][0] - 1},
    )
    shared["oauth"].retry_once_action = "oauth_vault.refresh_claim"
    shared["oauth"].retry_advance = 30.0

    assert vault.access_token(
        "github",
        refresher=lambda _record: {"access_token": "fresh", "expires_in": 60},
    ) == "fresh"
    assert shared["clock"][0] == 2_000_000_030.0


def test_oauth_ciphertext_swap_between_providers_is_rejected(shared):
    vault = oauth_vault.OAuthVault("acme")
    vault.put("github", {"access_token": "gh"})
    vault.put("slack", {"access_token": "sl"})
    document = shared["oauth"].documents["acme"]
    document["providers"]["slack"]["ciphertext"] = document["providers"][
        "github"
    ]["ciphertext"]
    document["providers"]["slack"]["record_sha256"] = document["providers"][
        "github"
    ]["record_sha256"]
    with pytest.raises(oauth_vault.OAuthVaultAuthorityError):
        vault.get("slack")


def test_oauth_legacy_file_blocks_then_imports_with_exact_digest(shared):
    tenant_id = "acme"
    # Mint the governed wrapped DEK, then model the historical whole-vault blob
    # that was sealed under that same tenant key before shared cutover.
    kms.tenant_dek(tenant_id)
    legacy_record = {
        "github": {
            "access_token": "legacy-secret",
            "refresh_token": "legacy-refresh",
            "expires_in": 3600,
            "obtained_at": shared["clock"][0],
        }
    }
    path = oauth_vault._legacy_oauth_path(tenant_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(
        kms.seal_text_for_tenant(tenant_id, json.dumps(legacy_record))
    )
    vault = oauth_vault.OAuthVault(tenant_id)
    with pytest.raises(
        oauth_vault.OAuthVaultAuthorityError,
        match="explicitly import",
    ):
        vault.get("github")

    assert oauth_vault.import_legacy_oauth_vault(tenant_id) == 1
    assert vault.get("github")["access_token"] == "legacy-secret"
    persisted = json.dumps(shared["oauth"].documents[tenant_id], sort_keys=True)
    assert "legacy-secret" not in persisted
    assert "legacy-refresh" not in persisted

    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(oauth_vault.OAuthVaultAuthorityError):
        vault.get("github")


def test_oauth_shared_outage_has_no_local_fallback(shared):
    shared["oauth"].backend = "unavailable"
    vault = oauth_vault.OAuthVault("acme")
    with pytest.raises(oauth_vault.OAuthVaultAuthorityError):
        vault.get("github")
    assert not oauth_vault._legacy_oauth_path("acme").exists()


def _browser_call(tenant_id: str, principal: str, args: dict) -> str:
    with _tenant(tenant_id), bind_principal(principal):
        return browser._run(args)


def test_browser_exact_principal_isolation_and_encrypted_authority(shared):
    assert "sealed entry" in _browser_call(
        "acme",
        "user:alice",
        {"op": "store", "name": "reports", "data": {"cookie": "secret-a"}},
    )
    assert _browser_call("acme", "user:alice ", {"op": "list"}) == "(vault empty)"
    assert _browser_call("beta", "user:alice", {"op": "list"}) == "(vault empty)"
    assert _browser_call("acme", "user:alice", {"op": "list"}) == "reports"
    persisted = json.dumps(
        shared["browser"].documents[("acme", "user:alice")],
        sort_keys=True,
    )
    assert "secret-a" not in persisted
    assert "user:alice" not in persisted


def test_browser_concurrent_entry_mutations_are_atomic(shared):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda index: _browser_call(
                "acme",
                "user:alice",
                {
                    "op": "store",
                    "name": f"entry-{index}",
                    "data": {"token": f"value-{index}"},
                },
            ),
            range(32),
        ))
    assert all("sealed entry" in result for result in results)
    entries = _browser_call("acme", "user:alice", {"op": "list"}).splitlines()
    assert len(entries) == 32


def test_browser_lost_response_retry_does_not_increment_generation(shared):
    shared["browser"].fail_after_commit_action = "browser_vault.put"
    args = {"op": "store", "name": "github", "data": {"cookie": "secret"}}
    assert _browser_call("acme", "user:alice", args).startswith("ERROR:")
    assert "sealed entry" in _browser_call("acme", "user:alice", args)
    entry = shared["browser"].documents[("acme", "user:alice")]["entries"][
        "github"
    ]
    assert entry["generation"] == 1


def test_browser_ciphertext_swap_between_names_is_rejected(shared):
    _browser_call(
        "acme",
        "user:alice",
        {"op": "store", "name": "one", "data": {"cookie": "one"}},
    )
    _browser_call(
        "acme",
        "user:alice",
        {"op": "store", "name": "two", "data": {"cookie": "two"}},
    )
    document = shared["browser"].documents[("acme", "user:alice")]
    document["entries"]["two"]["ciphertext"] = document["entries"]["one"][
        "ciphertext"
    ]
    document["entries"]["two"]["data_sha256"] = document["entries"]["one"][
        "data_sha256"
    ]
    with _tenant("acme"), bind_principal("user:alice"):
        vault = browser._shared_runtime_vault()
        with pytest.raises(browser.BrowserVaultAuthorityError):
            vault.load("two")


def test_shared_browser_outage_never_falls_back_to_local_files(shared):
    shared["browser"].backend = "unavailable"
    result = _browser_call(
        "acme",
        "user:alice",
        {"op": "store", "name": "x", "data": {"cookie": "secret"}},
    )
    assert result == "ERROR: browser auth vault authority is unavailable"
    key_path, data_path = browser._legacy_paths_for_authority("acme", "user:alice")
    assert not key_path.exists()
    assert not data_path.exists()


def test_browser_legacy_state_blocks_then_imports_exact_authority(shared):
    tenant_id = "acme"
    principal = "user:alice"
    key_path, data_path = browser._legacy_paths_for_authority(tenant_id, principal)
    key = browser.resolve_key(key_path)
    browser.Vault(key, data_path, _owned_parent=True).store(
        "github",
        {"cookie": "legacy-browser-secret"},
    )

    assert _browser_call(tenant_id, principal, {"op": "list"}) == (
        "ERROR: browser auth vault authority is unavailable"
    )
    assert browser.import_legacy_browser_auth_vault(tenant_id, principal) == 1
    assert _browser_call(tenant_id, principal, {"op": "list"}) == "github"
    assert "loaded 'github'" in _browser_call(
        tenant_id,
        principal,
        {"op": "load", "name": "github"},
    )
    persisted = json.dumps(
        shared["browser"].documents[(tenant_id, principal)],
        sort_keys=True,
    )
    assert "legacy-browser-secret" not in persisted

    browser.Vault(key, data_path, _owned_parent=True).store(
        "new-entry",
        {"cookie": "changed"},
    )
    assert _browser_call(tenant_id, principal, {"op": "list"}) == (
        "ERROR: browser auth vault authority is unavailable"
    )
