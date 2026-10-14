"""BYOK cloud KMS backends (AWS / GCP / Vault) behind the tenant_kms.KMS
Protocol. Fakes use real AES-GCM so context binding is actually exercised."""
from __future__ import annotations

import base64
import json
import os
import sys
import types

import pytest

pytest.importorskip("cryptography")
from cryptography.exceptions import InvalidTag  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from maverick import kms_backends  # noqa: E402
from maverick.crypto_at_rest import EncryptionUnavailable  # noqa: E402
from maverick.tenant import kms as tenant_kms  # noqa: E402

_FIXED_KEK = b"\x11" * 32


def _enc(plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_FIXED_KEK).encrypt(nonce, plaintext, aad)


def _dec(blob: bytes, aad: bytes) -> bytes:
    return AESGCM(_FIXED_KEK).decrypt(blob[:12], blob[12:], aad)


# ---- fake SDK clients (match each SDK's response shape) --------------------


class _FakeAws:
    def __init__(self):
        self.decrypt_key_ids = []

    def encrypt(self, KeyId, Plaintext, EncryptionContext):  # noqa: N803
        aad = json.dumps(EncryptionContext, sort_keys=True).encode()
        return {"CiphertextBlob": _enc(Plaintext, aad)}

    def decrypt(self, KeyId, CiphertextBlob, EncryptionContext):  # noqa: N803
        self.decrypt_key_ids.append(KeyId)
        aad = json.dumps(EncryptionContext, sort_keys=True).encode()
        return {"Plaintext": _dec(CiphertextBlob, aad)}


class _FakeGcp:
    def encrypt(self, request):
        aad = request["additional_authenticated_data"]
        ct = _enc(request["plaintext"], aad)
        return types.SimpleNamespace(ciphertext=ct)

    def decrypt(self, request):
        aad = request["additional_authenticated_data"]
        pt = _dec(request["ciphertext"], aad)
        return types.SimpleNamespace(plaintext=pt)


class _FakeVault:
    class _Transit:
        def encrypt_data(self, name, mount_point, plaintext, context):
            aad = context.encode()
            ct = _enc(base64.b64decode(plaintext), aad)
            return {"data": {"ciphertext": "vault:v1:" + base64.b64encode(ct).decode()}}

        def decrypt_data(self, name, mount_point, ciphertext, context):
            aad = context.encode()
            ct = base64.b64decode(ciphertext.split("vault:v1:", 1)[1])
            return {"data": {"plaintext": base64.b64encode(_dec(ct, aad)).decode()}}

    def __init__(self):
        self.secrets = types.SimpleNamespace(transit=self._Transit())


_BACKENDS = [
    lambda: kms_backends.AwsKmsKEK(key_id="k", client=_FakeAws()),
    lambda: kms_backends.GcpKmsKEK(key_id="k", client=_FakeGcp()),
    lambda: kms_backends.VaultTransitKMS(key_id="k", client=_FakeVault()),
]


@pytest.mark.parametrize("make", _BACKENDS)
def test_wrap_unwrap_round_trip(make):
    kms = make()
    dek = b"\x42" * 32
    ctx = b"tenant:acme"
    wrapped = kms.wrap(dek, context=ctx)
    assert wrapped != dek
    assert kms.unwrap(wrapped, context=ctx) == dek


@pytest.mark.parametrize("make", _BACKENDS)
def test_context_binding_enforced(make):
    kms = make()
    wrapped = kms.wrap(b"\x42" * 32, context=b"tenant:acme")
    with pytest.raises(InvalidTag):  # wrong context -> AEAD auth failure
        kms.unwrap(wrapped, context=b"tenant:evil")


def test_aws_unwrap_enforces_configured_key_id():
    client = _FakeAws()
    kms = kms_backends.AwsKmsKEK(key_id="tenant-key", client=client)

    wrapped = kms.wrap(b"\x42" * 32, context=b"tenant:acme")

    assert kms.unwrap(wrapped, context=b"tenant:acme") == b"\x42" * 32
    assert client.decrypt_key_ids == ["tenant-key"]


@pytest.mark.parametrize("make", _BACKENDS)
def test_wrong_provider_magic_rejected(make):
    kms = make()
    with pytest.raises(EncryptionUnavailable):
        kms.unwrap(b"NOT-OUR-MAGIC" + b"x" * 40, context=b"c")


# ---- Azure Key Vault / Managed HSM ----------------------------------------

_AZ_VAULT = "https://acme-kv.vault.azure.net"
_AZ_V1 = "a" * 32
_AZ_V2 = "b" * 32
_AZ_KEY = f"{_AZ_VAULT}/keys/tenant-kek"          # unversioned key identifier
_AZ_V1_ID = f"{_AZ_KEY}/{_AZ_V1}"
_AZ_V2_ID = f"{_AZ_KEY}/{_AZ_V2}"


class _FakeAzureCrypto:
    """Stands in for ``azure.keyvault.keys.crypto.CryptographyClient`` bound to one
    key identifier. The RSA step is simulated; the results carry the attribute
    names the SDK uses (``WrapResult.key_id``/``encrypted_key``, ``UnwrapResult.key``)."""

    def __init__(self, key_id, log, reported_id=None):
        self._key_id = key_id
        self._reported = reported_id or key_id
        self._log = log

    def wrap_key(self, algorithm, key):
        self._log.append(("wrap", self._key_id, algorithm))
        return types.SimpleNamespace(key_id=self._reported, encrypted_key=_enc(key, b""))

    def unwrap_key(self, algorithm, encrypted_key):
        self._log.append(("unwrap", self._key_id, algorithm))
        return types.SimpleNamespace(key_id=self._reported, key=_dec(encrypted_key, b""))


class _FakeAzureVault:
    """The ``client_factory`` injected into AzureKeyVaultKEK: one fake per key id."""

    def __init__(self, reported_id=None):
        self.log = []
        self._reported = reported_id

    def __call__(self, key_id):
        return _FakeAzureCrypto(key_id, self.log, self._reported)


def _azure_kek(key_id=_AZ_V1_ID, reported_id=None):
    fake = _FakeAzureVault(reported_id)
    return kms_backends.AzureKeyVaultKEK(key_id=key_id, client_factory=fake), fake


def test_azure_round_trip_wraps_under_rsa_oaep_256():
    kek, fake = _azure_kek()
    dek = b"\x42" * 32
    wrapped = kek.wrap(dek, context=b"tenant:acme")
    assert wrapped.startswith(kms_backends._AZURE_MAGIC)
    assert dek not in wrapped
    assert kek.unwrap(wrapped, context=b"tenant:acme") == dek
    assert fake.log[0] == ("wrap", _AZ_V1_ID, "RSA-OAEP-256")
    # A fresh key and nonce per wrap: the same DEK never wraps to the same bytes.
    assert kek.wrap(dek, context=b"tenant:acme") != wrapped


def test_azure_wrong_context_fails():
    kek, _ = _azure_kek()
    wrapped = kek.wrap(b"\x42" * 32, context=b"tenant:acme")
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(wrapped, context=b"tenant:evil")
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(wrapped)  # no context is a different context too


def test_azure_tampered_ciphertext_fails():
    kek, _ = _azure_kek()
    tampered = bytearray(kek.wrap(b"\x42" * 32, context=b"c"))
    tampered[-1] ^= 0x01
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(bytes(tampered), context=b"c")


def test_azure_every_truncation_and_trailing_bytes_fail():
    kek, _ = _azure_kek()
    wrapped = kek.wrap(b"\x42" * 32, context=b"c")
    for cut in range(len(kms_backends._AZURE_MAGIC), len(wrapped)):
        with pytest.raises(EncryptionUnavailable):
            kek.unwrap(wrapped[:cut], context=b"c")
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(wrapped + b"\x00", context=b"c")


def test_azure_rejects_empty_envelope_fields():
    kek, fake = _azure_kek()
    empty_key = kms_backends._AZURE_MAGIC + b"\x00\x00" + b"\x00\x05" + b"x" * 5 + b"\x00" * 28
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(empty_key, context=b"c")
    assert not any(op == "unwrap" for op, *_ in fake.log)


@pytest.mark.parametrize("other_key_id", [
    f"https://evil-kv.vault.azure.net/keys/tenant-kek/{_AZ_V1}",   # another vault
    f"{_AZ_VAULT}/keys/other-kek/{_AZ_V1}",                        # another key name
])
def test_azure_refuses_a_stored_key_from_another_vault_or_key(other_key_id):
    kek, _ = _azure_kek()
    wrapped = kek.wrap(b"\x42" * 32, context=b"c")
    other, fake = _azure_kek(key_id=other_key_id)
    with pytest.raises(EncryptionUnavailable):
        other.unwrap(wrapped, context=b"c")
    assert not any(op == "unwrap" for op, *_ in fake.log)


def test_azure_accepts_another_version_of_the_same_key():
    kek_v1, _ = _azure_kek(key_id=_AZ_V1_ID)
    wrapped = kek_v1.wrap(b"\x42" * 32, context=b"c")
    for configured in (_AZ_V2_ID, _AZ_KEY):   # rotated to v2, or unversioned
        rotated, fake = _azure_kek(key_id=configured)
        assert rotated.unwrap(wrapped, context=b"c") == b"\x42" * 32
        # The DEK is opened with the version that wrapped it, not the configured one.
        assert ("unwrap", _AZ_V1_ID, "RSA-OAEP-256") in fake.log


@pytest.mark.parametrize("configured, reported", [
    (_AZ_KEY, _AZ_KEY),                                            # no version
    (_AZ_V1_ID, f"https://evil-kv.vault.azure.net/keys/tenant-kek/{_AZ_V1}"),
])
def test_azure_wrap_refuses_a_result_that_is_not_a_pinned_version_of_the_key(configured, reported):
    kek, _ = _azure_kek(key_id=configured, reported_id=reported)
    with pytest.raises(EncryptionUnavailable):
        kek.wrap(b"\x42" * 32, context=b"c")


@pytest.mark.parametrize("bad", [
    "http://acme-kv.vault.azure.net/keys/tenant-kek",              # not https
    "https://acme-kv.vault.azure.net/secrets/tenant-kek",          # a secret, not a key
    "https://acme-kv.vault.azure.net/keys/",                       # no key name
    f"{_AZ_KEY}/{_AZ_V1}/extra",                                   # too many segments
    "tenant-kek",                                                  # a bare name
    "https://user@acme-kv.vault.azure.net/keys/tenant-kek",        # userinfo
    "https://acme-kv.vault.azure.net:8443/keys/tenant-kek",        # explicit port
    "https://acme-kv.vault.azure.net/keys/tenant-kek?api-version=7.4",
    "https://acme-kv.vault.azure.net/keys/tenant/kek",             # slash in the name
])
def test_azure_rejects_a_malformed_key_id(bad):
    with pytest.raises(EncryptionUnavailable):
        kms_backends.AzureKeyVaultKEK(key_id=bad, client_factory=_FakeAzureVault())


@pytest.mark.parametrize("good", [
    _AZ_KEY,
    _AZ_V1_ID,
    "https://acme-hsm.managedhsm.azure.net/keys/tenant-kek/" + _AZ_V1,
])
def test_azure_accepts_vault_and_managed_hsm_key_ids(good):
    kek = kms_backends.AzureKeyVaultKEK(key_id=good, client_factory=_FakeAzureVault())
    assert kek.key_id == good


def test_azure_rejects_other_providers_envelopes():
    vault_wrapped = kms_backends.VaultTransitKMS(
        key_id="k", client=_FakeVault()).wrap(b"\x42" * 32, context=b"c")
    kek, _ = _azure_kek()
    with pytest.raises(EncryptionUnavailable):
        kek.unwrap(vault_wrapped, context=b"c")


def test_azure_missing_sdk_gives_the_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "azure.keyvault.keys.crypto", None)
    kek = kms_backends.AzureKeyVaultKEK(key_id=_AZ_V1_ID)  # no fake: the real SDK path
    with pytest.raises(EncryptionUnavailable, match=r"maverick-core\[azure-kms\]"):
        kek.wrap(b"\x42" * 32, context=b"c")


def test_build_cloud_kms_azure_dispatch(monkeypatch):
    monkeypatch.delenv("MAVERICK_KMS_KEY_ID", raising=False)
    kek = kms_backends.build_cloud_kms("azure", {"key_id": _AZ_V1_ID})
    assert isinstance(kek, kms_backends.AzureKeyVaultKEK)
    assert kek.key_id == _AZ_V1_ID
    tenant = kms_backends.build_cloud_kms("azure", {"key_id": _AZ_V1_ID}, key_id=_AZ_V2_ID)
    assert tenant.key_id == _AZ_V2_ID  # a pinned tenant key outranks the config key
    with pytest.raises(EncryptionUnavailable):
        kms_backends.build_cloud_kms("azure", {"key_id": "operator-global"})


def test_azure_key_id_is_required(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config", lambda: {"kms": {}})
    monkeypatch.delenv("MAVERICK_KMS_KEY_ID", raising=False)
    with pytest.raises(EncryptionUnavailable):
        kms_backends.AzureKeyVaultKEK()


# ---- factory + get_kms dispatch -------------------------------------------


def test_build_cloud_kms_dispatch(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config",
                        lambda: {"kms": {"provider": "aws", "key_id": "k"}})
    assert isinstance(kms_backends.build_cloud_kms("aws"), kms_backends.AwsKmsKEK)
    assert isinstance(kms_backends.build_cloud_kms("gcp"), kms_backends.GcpKmsKEK)
    assert isinstance(kms_backends.build_cloud_kms("vault"),
                      kms_backends.VaultTransitKMS)


@pytest.mark.parametrize("provider", ["aws", "gcp", "vault"])
def test_explicit_key_id_beats_the_server_wide_env_key(monkeypatch, provider):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "operator-global")
    kms = kms_backends.build_cloud_kms(
        provider, {"key_id": "cfg-key"}, key_id="tenant-byok")
    assert kms.key_id == "tenant-byok"


@pytest.mark.parametrize("provider", ["aws", "gcp", "vault"])
def test_env_key_is_used_when_no_explicit_key_id(monkeypatch, provider):
    monkeypatch.setenv("MAVERICK_KMS_KEY_ID", "operator-global")
    kms = kms_backends.build_cloud_kms(provider, {"key_id": "cfg-key"})
    assert kms.key_id == "operator-global"


def test_build_cloud_kms_unknown_fails():
    with pytest.raises(EncryptionUnavailable):
        kms_backends.build_cloud_kms("oracle")


def test_missing_key_id_fails(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config", lambda: {"kms": {}})
    monkeypatch.delenv("MAVERICK_KMS_KEY_ID", raising=False)
    with pytest.raises(EncryptionUnavailable):
        kms_backends.AwsKmsKEK()


def test_get_kms_routes_to_cloud(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config",
                        lambda: {"kms": {"provider": "vault", "key_id": "k"}})
    assert isinstance(tenant_kms.get_kms(), kms_backends.VaultTransitKMS)


def test_get_kms_routes_to_azure(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config",
                        lambda: {"kms": {"provider": "azure", "key_id": _AZ_V1_ID}})
    assert isinstance(tenant_kms.get_kms(), kms_backends.AzureKeyVaultKEK)


def test_get_kms_local_default(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config", lambda: {"kms": {"provider": "local"}})
    assert isinstance(tenant_kms.get_kms(), tenant_kms.LocalKMS)


def test_get_kms_unknown_provider_fails(monkeypatch):
    monkeypatch.setattr("maverick.config.load_config", lambda: {"kms": {"provider": "oracle"}})
    with pytest.raises(EncryptionUnavailable):
        tenant_kms.get_kms()


# ---- integration: tenant seal/unseal through a cloud KEK ------------------


def test_tenant_seal_through_cloud_kms(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_CLIENT_ID", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    tenant_kms._clear_cache()
    kms = kms_backends.AwsKmsKEK(key_id="k", client=_FakeAws())
    blob = tenant_kms.seal_for_tenant("acme", b"phi-data", kms=kms)
    assert b"phi-data" not in blob
    assert tenant_kms.unseal_for_tenant("acme", blob, kms=kms) == b"phi-data"
    tenant_kms._clear_cache()
