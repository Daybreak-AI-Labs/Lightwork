"""Cloud KMS backends (BYOK) — the KEK lives in the customer's HSM/KMS.

The default :class:`maverick.tenant.kms.LocalKMS` derives the KEK in-process. A
regulated client that mandates **customer-managed keys** wants the KEK to never
leave their KMS/HSM; these backends implement the :class:`~maverick.tenant.kms.KMS`
``wrap``/``unwrap`` Protocol by delegating to the cloud KMS Encrypt/Decrypt API,
binding the tenant ``context`` as the KMS encryption context / AAD so a wrapped
DEK can't be unwrapped under the wrong tenant/purpose.

Selected by ``[kms] provider`` (``aws`` / ``gcp`` / ``vault`` / ``azure``). SDKs are
lazy imports (optional extras); a missing SDK raises a clear install hint rather
than falling back to in-process keys. Clients are injectable for testing.

Config (``~/.maverick/config.toml``)::

    [kms]
    provider = "aws"                       # aws | gcp | vault | azure | local
    key_id   = "arn:aws:kms:us-east-1:...:key/<uuid>"   # AWS key id/ARN,
                                           # GCP crypto-key resource name,
                                           # Vault transit key name, or Azure
                                           # https://<vault>.vault.azure.net/keys/<name>[/<version>]
                                           # (also a Managed HSM key URL)
    region   = "us-east-1"                 # AWS (or AWS_REGION)
    address  = "https://vault.internal:8200"   # Vault (or VAULT_ADDR)
    mount    = "transit"                   # Vault transit mount

Auth uses each SDK's standard chain (IAM role / ADC / VAULT_TOKEN /
DefaultAzureCredential).
"""
from __future__ import annotations

import base64
import os
import re
from typing import Any
from urllib.parse import urlsplit

from .crypto_at_rest import EncryptionUnavailable

_AWS_MAGIC = b"MVKKMS-AWS1\n"
_GCP_MAGIC = b"MVKKMS-GCP1\n"
_VAULT_MAGIC = b"MVKKMS-VLT1\n"
_AZURE_MAGIC = b"MVKKMS-AZR1\n"


def _kms_cfg() -> dict:
    try:
        from .config import load_config
        return (load_config() or {}).get("kms") or {}
    except Exception:  # pragma: no cover - config never blocks key setup
        return {}


def _require(value: str | None, what: str) -> str:
    if not value:
        raise EncryptionUnavailable(f"[kms] {what} is required for this provider")
    return value


def _ctx_b64(context: bytes | None) -> str:
    return base64.b64encode(context or b"").decode("ascii")


def _split_magic(magic: bytes, wrapped: bytes) -> bytes:
    if not wrapped.startswith(magic):
        raise EncryptionUnavailable("wrapped DEK is not from this KMS provider (bad magic)")
    return wrapped[len(magic):]


class AwsKmsKEK:
    """KEK in AWS KMS. ``wrap`` = ``kms:Encrypt`` with the tenant context as the
    EncryptionContext; ``unwrap`` = ``kms:Decrypt``."""

    def __init__(self, *, key_id: str | None = None, region: str | None = None,
                 client: Any | None = None, cfg: dict | None = None):
        cfg = cfg if cfg is not None else _kms_cfg()
        self.key_id = _require(key_id or os.environ.get("MAVERICK_KMS_KEY_ID")
                               or cfg.get("key_id"), "key_id")
        self.region = region or cfg.get("region") or os.environ.get("AWS_REGION")
        self._client = client

    def _kms(self):
        if self._client is None:
            try:
                import boto3  # type: ignore
            except ImportError as e:
                raise EncryptionUnavailable(
                    "AWS KMS backend needs boto3 (pip install boto3)") from e
            self._client = boto3.client("kms", region_name=self.region)
        return self._client

    def wrap(self, dek: bytes, *, context: bytes | None = None) -> bytes:
        resp = self._kms().encrypt(
            KeyId=self.key_id, Plaintext=dek,
            EncryptionContext={"maverick": _ctx_b64(context)})
        return _AWS_MAGIC + resp["CiphertextBlob"]

    def unwrap(self, wrapped: bytes, *, context: bytes | None = None) -> bytes:
        ct = _split_magic(_AWS_MAGIC, wrapped)
        resp = self._kms().decrypt(
            KeyId=self.key_id,
            CiphertextBlob=ct,
            EncryptionContext={"maverick": _ctx_b64(context)})
        return resp["Plaintext"]


class GcpKmsKEK:
    """KEK in Google Cloud KMS. ``wrap``/``unwrap`` use Encrypt/Decrypt with the
    tenant context as ``additional_authenticated_data``."""

    def __init__(self, *, key_id: str | None = None, client: Any | None = None,
                 cfg: dict | None = None):
        cfg = cfg if cfg is not None else _kms_cfg()
        # GCP uses the full crypto-key resource name as the id.
        self.key_id = _require(key_id or os.environ.get("MAVERICK_KMS_KEY_ID")
                               or cfg.get("key_id"), "key_id")
        self._client = client

    def _kms(self):
        if self._client is None:
            try:
                from google.cloud import kms  # type: ignore
            except ImportError as e:
                raise EncryptionUnavailable(
                    "GCP KMS backend needs google-cloud-kms "
                    "(pip install google-cloud-kms)") from e
            self._client = kms.KeyManagementServiceClient()
        return self._client

    def wrap(self, dek: bytes, *, context: bytes | None = None) -> bytes:
        resp = self._kms().encrypt(request={
            "name": self.key_id, "plaintext": dek,
            "additional_authenticated_data": context or b""})
        return _GCP_MAGIC + resp.ciphertext

    def unwrap(self, wrapped: bytes, *, context: bytes | None = None) -> bytes:
        ct = _split_magic(_GCP_MAGIC, wrapped)
        resp = self._kms().decrypt(request={
            "name": self.key_id, "ciphertext": ct,
            "additional_authenticated_data": context or b""})
        return resp.plaintext


class VaultTransitKMS:
    """KEK in HashiCorp Vault's transit engine. The tenant context is passed as
    the transit ``context`` (the transit key must be created with
    ``derived=true`` so the context is bound into key derivation)."""

    def __init__(self, *, key_id: str | None = None, address: str | None = None,
                 token: str | None = None, mount: str | None = None,
                 client: Any | None = None, cfg: dict | None = None):
        cfg = cfg if cfg is not None else _kms_cfg()
        self.key_id = _require(key_id or os.environ.get("MAVERICK_KMS_KEY_ID")
                               or cfg.get("key_id"), "key_id")
        self.address = address or cfg.get("address") or os.environ.get("VAULT_ADDR")
        self.token = token or os.environ.get("VAULT_TOKEN")
        self.mount = mount or cfg.get("mount") or "transit"
        self._client = client

    def _vault(self):
        if self._client is None:
            try:
                import hvac  # type: ignore
            except ImportError as e:
                raise EncryptionUnavailable(
                    "Vault KMS backend needs hvac (pip install hvac)") from e
            self._client = hvac.Client(url=self.address, token=self.token)
        return self._client

    def wrap(self, dek: bytes, *, context: bytes | None = None) -> bytes:
        resp = self._vault().secrets.transit.encrypt_data(
            name=self.key_id, mount_point=self.mount,
            plaintext=base64.b64encode(dek).decode("ascii"),
            context=_ctx_b64(context))
        ciphertext = resp["data"]["ciphertext"]  # "vault:v1:..."
        return _VAULT_MAGIC + ciphertext.encode("utf-8")

    def unwrap(self, wrapped: bytes, *, context: bytes | None = None) -> bytes:
        ciphertext = _split_magic(_VAULT_MAGIC, wrapped).decode("utf-8")
        resp = self._vault().secrets.transit.decrypt_data(
            name=self.key_id, mount_point=self.mount,
            ciphertext=ciphertext, context=_ctx_b64(context))
        return base64.b64decode(resp["data"]["plaintext"])


# Azure's RSA key wrap takes no context, so the DEK is sealed under a random AES key
# that is itself wrapped. Layout after the magic: 2-byte length + wrapped key, 2-byte
# length + versioned key id (UTF-8), 12-byte nonce, AES-GCM ciphertext (with tag).
_AZURE_AAD = b"maverick-kms-azure-v1\n"
# KeyWrapAlgorithm.rsa_oaep_256 of azure-keyvault-keys, given by value so building the
# client needs no SDK import.
_AZURE_WRAP_ALG = "RSA-OAEP-256"
_AZURE_KEY_PATH = re.compile(r"/keys/([A-Za-z0-9-]{1,127})(?:/([A-Za-z0-9]{32}))?")
_AZURE_HOST = re.compile(r"[a-z0-9.-]{1,253}")
_GCM_NONCE_BYTES = 12
_GCM_TAG_BYTES = 16


def _azure_key_parts(key_id: str) -> tuple[str, str, str] | None:
    """``(vault host, key name, version or "")`` of a Key Vault or Managed HSM key
    identifier ``https://<vault>/keys/<name>[/<version>]``; None if it is not one."""
    try:
        parts = urlsplit(key_id)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    match = _AZURE_KEY_PATH.fullmatch(parts.path)
    if (parts.scheme != "https" or match is None or not _AZURE_HOST.fullmatch(host)
            or ":" in parts.netloc or "@" in parts.netloc
            or parts.query or parts.fragment):
        return None
    return host, match.group(1).lower(), match.group(2) or ""


def _len2(field: bytes) -> bytes:
    if not 0 < len(field) <= 0xFFFF:
        raise EncryptionUnavailable("Azure KMS envelope field has an invalid length")
    return len(field).to_bytes(2, "big") + field


def _read_len2(buf: bytes, pos: int) -> tuple[bytes, int]:
    if len(buf) < pos + 2:
        raise EncryptionUnavailable("Azure KMS envelope is truncated or malformed")
    end = pos + 2 + int.from_bytes(buf[pos:pos + 2], "big")
    if end == pos + 2 or end > len(buf):
        raise EncryptionUnavailable("Azure KMS envelope is truncated or malformed")
    return buf[pos + 2:end], end


class AzureKeyVaultKEK:
    """KEK in Azure Key Vault or Managed HSM. Key Vault's RSA wrap has no context
    parameter, so the tenant context is bound locally: ``wrap`` RSA-OAEP-256-wraps a
    fresh random 32-byte key with the vault key (``CryptographyClient.wrap_key``) and
    seals the DEK under that key with AES-256-GCM, the context as associated data.
    The envelope keeps the versioned key id the service reported; ``unwrap`` accepts
    only an id for the same vault and key name (another version is fine, so a
    rotated key still opens existing DEKs)."""

    def __init__(self, *, key_id: str | None = None, client_factory: Any | None = None,
                 cfg: dict | None = None):
        cfg = cfg if cfg is not None else _kms_cfg()
        self.key_id = _require(key_id or os.environ.get("MAVERICK_KMS_KEY_ID")
                               or cfg.get("key_id"), "key_id")
        parts = _azure_key_parts(self.key_id)
        if parts is None:
            raise EncryptionUnavailable(
                "[kms] key_id for azure must be a Key Vault key URL: "
                "https://<vault>.vault.azure.net/keys/<name>[/<version>]")
        self._host, self._name = parts[0], parts[1]
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}

    def _crypto(self, key_id: str):
        """The CryptographyClient for one key identifier, built on first use."""
        if key_id not in self._clients:
            if self._client_factory is not None:
                self._clients[key_id] = self._client_factory(key_id)
            else:
                try:
                    from azure.identity import DefaultAzureCredential  # type: ignore
                    from azure.keyvault.keys.crypto import CryptographyClient  # type: ignore
                except ImportError as e:
                    raise EncryptionUnavailable(
                        "Azure Key Vault KMS backend needs azure-keyvault-keys and "
                        'azure-identity (pip install "maverick-core[azure-kms]")') from e
                self._clients[key_id] = CryptographyClient(key_id, DefaultAzureCredential())
        return self._clients[key_id]

    def _same_key(self, key_id: str) -> bool:
        parts = _azure_key_parts(key_id)
        return parts is not None and parts[0] == self._host and parts[1] == self._name

    def wrap(self, dek: bytes, *, context: bytes | None = None) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aes_key = os.urandom(32)
        wrapped = self._crypto(self.key_id).wrap_key(_AZURE_WRAP_ALG, aes_key)
        key_id = str(wrapped.key_id or "")
        parts = _azure_key_parts(key_id)
        if not self._same_key(key_id) or not parts[2]:
            raise EncryptionUnavailable(
                "Azure Key Vault did not return a versioned id for the configured key")
        nonce = os.urandom(_GCM_NONCE_BYTES)
        sealed = AESGCM(aes_key).encrypt(nonce, dek, _AZURE_AAD + (context or b""))
        return (_AZURE_MAGIC + _len2(bytes(wrapped.encrypted_key))
                + _len2(key_id.encode("utf-8")) + nonce + sealed)

    def unwrap(self, wrapped: bytes, *, context: bytes | None = None) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        body = _split_magic(_AZURE_MAGIC, wrapped)
        encrypted_key, pos = _read_len2(body, 0)
        key_id_bytes, pos = _read_len2(body, pos)
        if len(body) < pos + _GCM_NONCE_BYTES + _GCM_TAG_BYTES:
            raise EncryptionUnavailable("Azure KMS envelope is truncated or malformed")
        nonce, sealed = body[pos:pos + _GCM_NONCE_BYTES], body[pos + _GCM_NONCE_BYTES:]
        try:
            key_id = key_id_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise EncryptionUnavailable("Azure KMS envelope is malformed") from e
        parts = _azure_key_parts(key_id)
        if not self._same_key(key_id) or not parts[2]:
            raise EncryptionUnavailable(
                "Azure KMS envelope was wrapped under a different Key Vault key")
        aes_key = self._crypto(key_id).unwrap_key(_AZURE_WRAP_ALG, encrypted_key).key
        if len(aes_key) != 32:
            raise EncryptionUnavailable("Azure KMS unwrapped key is malformed")
        try:
            return AESGCM(aes_key).decrypt(nonce, sealed, _AZURE_AAD + (context or b""))
        except InvalidTag as e:
            raise EncryptionUnavailable(
                "cannot open Azure KMS envelope: wrong context or altered ciphertext") from e


_PROVIDERS = {"aws": AwsKmsKEK, "gcp": GcpKmsKEK, "vault": VaultTransitKMS,
              "azure": AzureKeyVaultKEK}


def build_cloud_kms(provider: str, cfg: dict | None = None, *,
                    key_id: str | None = None):
    """Construct the cloud KMS backend for ``provider`` (aws/gcp/vault/azure).

    ``cfg`` is an explicit ``[kms]`` section (key_id/region/address/mount). Pass
    a tenant's resolved config for deterministic per-tenant BYOK — without it the
    backend reads the ambient ``load_config()`` (which depends on the active
    tenant context). ``key_id`` is a key the CALLER has pinned for this backend
    (a workspace's own BYOK key); it outranks ``MAVERICK_KMS_KEY_ID``, which is
    the server-wide fallback and must never replace a customer's own key.
    Raises :class:`EncryptionUnavailable` for an unknown
    provider (fail-closed — never silently fall back to in-process keys).
    Construction is cheap; the SDK client is built lazily on first wrap/unwrap.
    """
    cls = _PROVIDERS.get((provider or "").strip().lower())
    if cls is None:
        raise EncryptionUnavailable(
            f"[kms] provider={provider!r} is not a known backend "
            f"(expected one of: local, {', '.join(sorted(_PROVIDERS))})")
    return cls(key_id=key_id, cfg=cfg)


__all__ = ["AwsKmsKEK", "AzureKeyVaultKEK", "GcpKmsKEK", "VaultTransitKMS", "build_cloud_kms"]
