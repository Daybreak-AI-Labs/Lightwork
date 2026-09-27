"""Context-bound sealing and the per-tenant key default."""
from __future__ import annotations

import pytest
from maverick import crypto_at_rest as c
from maverick.paths import tenant_scope


def test_bound_blob_opens_only_for_same_tenant_and_context():
    blob = c.seal_bound(b"payroll", "attachment/1/a.csv")
    assert c.is_bound(blob) and b"payroll" not in blob
    assert c.unseal_bound(blob, "attachment/1/a.csv") == b"payroll"
    with pytest.raises(c.EncryptionUnavailable):
        c.unseal_bound(blob, "attachment/2/a.csv")
    with tenant_scope(tenant="acme"), pytest.raises(c.EncryptionUnavailable):
        c.unseal_bound(blob, "attachment/1/a.csv")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(c.EncryptionUnavailable):
        c.unseal_bound(tampered, "attachment/1/a.csv")
    with pytest.raises(c.EncryptionUnavailable):
        c.unseal_bound(blob[:20], "attachment/1/a.csv")


def test_bound_blobs_survive_key_rotation():
    before = c.seal_bound(b"old", "ctx")
    c.rotate_at_rest_key()
    after = c.seal_bound(b"new", "ctx")
    assert before[8:16] != after[8:16]  # different embedded key ids
    assert c.unseal_bound(before, "ctx") == b"old"
    assert c.unseal_bound(after, "ctx") == b"new"


def test_per_tenant_mode_uses_the_tenant_key(monkeypatch):
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "1")
    monkeypatch.setenv("MAVERICK_KMS_KEK", "27" * 32)  # synthetic test key
    from maverick.tenant import kms
    monkeypatch.setattr(kms, "_dek_cache", {})
    with tenant_scope(tenant="acme"):
        blob = c.seal_bound(b"acme data", "ctx")
        assert blob[len(c._BOUND_MAGIC):len(c._BOUND_MAGIC) + 1] == b"T"
        assert c.unseal_bound(blob, "ctx") == b"acme data"
    with tenant_scope(tenant="beta"), pytest.raises(c.EncryptionUnavailable):
        c.unseal_bound(blob, "ctx")


@pytest.mark.parametrize("enterprise,config,env,expected", [
    (True, {}, None, True),                        # enterprise default: per-tenant keys
    (False, {}, None, False),                      # single-tenant default: one key
    (True, {"per_tenant": False}, None, False),    # explicit config wins
    (False, {"per_tenant": True}, None, True),
    (True, {}, "0", False),                        # env wins over everything
])
def test_per_tenant_default_follows_enterprise_mode(monkeypatch, enterprise, config, env, expected):
    import maverick.config as config_mod
    import maverick.enterprise as ent
    if env is None:
        monkeypatch.delenv("MAVERICK_ENCRYPT_PER_TENANT", raising=False)
    else:
        monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", env)
    monkeypatch.setattr(config_mod, "load_config", lambda: {"encryption": config})
    monkeypatch.setattr(ent, "enterprise_enabled", lambda: enterprise)
    assert c.per_tenant_at_rest() is expected
