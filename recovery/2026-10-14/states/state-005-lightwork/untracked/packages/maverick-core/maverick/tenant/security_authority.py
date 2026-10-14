"""Shared tenant policy and wrapped-DEK bootstrap authority.

Hosted replicas cannot use ``tenants/<id>/config.toml`` or
``keys/dek.wrapped`` as authority: those files are node-local and can diverge.
This module moves both families into tenant-scoped governed Postgres records.

The two records are deliberately encrypted with the deployment at-rest key,
not the tenant DEK.  Encrypting the record containing a KMS-wrapped tenant DEK
with that same tenant DEK would be circular.  The wrapped value remains KMS
ciphertext, RLS still scopes the row to the exact tenant, and ordinary tenant
records continue to use per-tenant envelope encryption.

Tenant policy stores raw configuration placeholders/references.  Literal
provider/KMS credentials are rejected so secrets remain in an external secret
provider or environment and are interpolated only in process memory.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from ..authority_documents import AuthorityDocumentStore
from ..crypto_at_rest import deployment_key_scope
from ..file_lock import atomic_read_bytes
from ..paths import (
    _tenant_segment,
    canonical_tenant_id,
    maverick_home,
    reset_tenant,
    set_tenant,
)

POLICY_SCHEMA_VERSION = 1
WRAPPED_DEK_SCHEMA_VERSION = 1

_MAX_POLICY_BYTES = 1024 * 1024
_MAX_POLICY_DEPTH = 32
_MAX_POLICY_ITEMS = 50_000
_MAX_WRAPPED_DEK_BYTES = 1024 * 1024
_MAX_LEGACY_FILE_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV_PLACEHOLDER = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")
_SECRET_REFERENCES = (
    "env://",
    "secret://",
    "vault://",
    "aws-sm://",
    "gcp-sm://",
    "azure-kv://",
)
_SECRET_KEYS = frozenset({
    "api_key",
    "access_key",
    "access_token",
    "client_secret",
    "connection_string",
    "credential",
    "credentials",
    "database_url",
    "dsn",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
})


class TenantSecurityAuthorityError(RuntimeError):
    """Shared tenant security state is unavailable or cannot be trusted."""


class WrappedDekConflict(TenantSecurityAuthorityError):
    """The wrapped tenant DEK changed after a caller prepared a replacement."""


class _NoAuthorityMutation(Exception):
    def __init__(self, result: object) -> None:
        super().__init__("tenant security authority mutation is already satisfied")
        self.result = result


@dataclass(frozen=True)
class WrappedDekRecord:
    ciphertext: bytes
    ciphertext_sha256: str
    policy_digest: str
    generation: int
    created_at: float
    rotated_at: float | None


def shared_security_authority_enabled() -> bool:
    """Whether this process selected governed Postgres authority."""
    from ..governed_records import configured_governed_records_backend

    return configured_governed_records_backend() == "postgres"


def _tenant_id(value: str) -> str:
    try:
        tenant_id = canonical_tenant_id(value)
    except Exception as exc:
        raise TenantSecurityAuthorityError("tenant identity is invalid") from exc
    if tenant_id != value:
        # Callers must use the canonical identity already admitted by the tenant
        # registry; silently normalizing here could address a different RLS row.
        raise TenantSecurityAuthorityError("tenant identity is not canonical")
    return tenant_id


@contextmanager
def tenant_authority_scope(tenant_id: str):
    """Bind one exact tenant for shared record I/O and restore ambient scope."""
    tenant_id = _tenant_id(tenant_id)
    token = set_tenant(tenant_id)
    try:
        yield
    finally:
        reset_tenant(token)


@contextmanager
def _bootstrap_scope(tenant_id: str):
    """Break config/KMS recursion only around bootstrap record operations."""
    from ..config import (
        without_shared_dashboard_settings,
        without_shared_tenant_policy,
    )

    with (
        tenant_authority_scope(tenant_id),
        without_shared_dashboard_settings(),
        without_shared_tenant_policy(),
        deployment_key_scope(),
    ):
        yield


def _new_policy_store(_tenant_id: str) -> AuthorityDocumentStore[Any]:
    return AuthorityDocumentStore(
        "tenant_policy_overlays",
        "TPO",
        "tenant_policy_overlay",
        document_name="state",
        required_backend="postgres",
    )


def _new_wrapped_dek_store(_tenant_id: str) -> AuthorityDocumentStore[Any]:
    return AuthorityDocumentStore(
        "tenant_wrapped_deks",
        "TDK",
        "tenant_wrapped_dek",
        document_name="state",
        required_backend="postgres",
    )


def _require_postgres(store: object) -> None:
    try:
        backend = str(store.backend_kind)  # type: ignore[attr-defined]
    except Exception as exc:
        raise TenantSecurityAuthorityError(
            "shared tenant security authority is unavailable"
        ) from exc
    if backend != "postgres":
        raise TenantSecurityAuthorityError(
            "shared tenant security authority requires governed Postgres"
        )


def _positive_time(value: object, *, label: str) -> float:
    if isinstance(value, bool):
        raise TenantSecurityAuthorityError(f"{label} is invalid")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TenantSecurityAuthorityError(f"{label} is invalid") from exc
    if not math.isfinite(result) or result <= 0:
        raise TenantSecurityAuthorityError(f"{label} is invalid")
    return result


def _canonical_json(value: object, *, label: str, max_bytes: int) -> bytes:
    try:
        raw = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise TenantSecurityAuthorityError(f"{label} is not bounded JSON") from exc
    if len(raw) > max_bytes:
        raise TenantSecurityAuthorityError(f"{label} exceeds its size bound")
    return raw


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise TenantSecurityAuthorityError(f"{label} is invalid")
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.strip().lower()
    return (
        lowered in _SECRET_KEYS
        or lowered.endswith("_api_key")
        or lowered.endswith("_access_token")
        or lowered.endswith("_client_secret")
        or lowered.endswith("_connection_string")
        or lowered.endswith("_database_url")
        or lowered.endswith("_dsn")
        or lowered.endswith("_password")
        or lowered.endswith("_private_key")
        or lowered.endswith("_refresh_token")
        or lowered.endswith("_secret")
        or lowered.endswith("_secret_key")
        or lowered.endswith("_token")
    )


def _external_secret_reference(value: str) -> bool:
    stripped = value.strip()
    return (
        not stripped
        or bool(_ENV_PLACEHOLDER.fullmatch(stripped))
        or stripped.lower().startswith(_SECRET_REFERENCES)
    )


def _validate_policy_value(
    value: object,
    *,
    path: tuple[str, ...] = (),
    depth: int = 0,
    counter: list[int] | None = None,
) -> None:
    if depth > _MAX_POLICY_DEPTH:
        raise TenantSecurityAuthorityError("tenant policy nesting is too deep")
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if counter[0] > _MAX_POLICY_ITEMS:
        raise TenantSecurityAuthorityError("tenant policy has too many values")
    if value is None or isinstance(value, (bool, int, str)):
        if (
            isinstance(value, str)
            and path
            and _is_secret_key(path[-1])
            and not _external_secret_reference(value)
        ):
            raise TenantSecurityAuthorityError(
                "tenant policy secrets must use an external secret reference"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TenantSecurityAuthorityError("tenant policy has a non-finite value")
        return
    if isinstance(value, list):
        for item in value:
            _validate_policy_value(
                item,
                path=path,
                depth=depth + 1,
                counter=counter,
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 256
                or not key.isprintable()
            ):
                raise TenantSecurityAuthorityError("tenant policy has an invalid key")
            _validate_policy_value(
                item,
                path=path + (key,),
                depth=depth + 1,
                counter=counter,
            )
        return
    raise TenantSecurityAuthorityError("tenant policy contains an unsupported value")


def validate_policy_overlay(value: object) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict):
        raise TenantSecurityAuthorityError("tenant policy overlay must be an object")
    overlay = copy.deepcopy(value)
    _validate_policy_value(overlay)
    raw = _canonical_json(
        overlay,
        label="tenant policy overlay",
        max_bytes=_MAX_POLICY_BYTES,
    )
    return overlay, _digest(raw)


def _validate_policy_document(
    document: object,
    *,
    tenant_id: str,
    initialize: bool,
    now: float | None = None,
) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        raise TenantSecurityAuthorityError("tenant policy document is malformed")
    if not document:
        if not initialize:
            return None
        if now is None:
            raise TenantSecurityAuthorityError("tenant policy timestamp is unavailable")
        empty, content_digest = validate_policy_overlay({})
        document.update({
            "schema_version": POLICY_SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "overlay": empty,
            "content_digest": content_digest,
            "source": "empty",
            "legacy_sha256": None,
            "created_at": now,
            "updated_at": now,
        })
    if set(document) != {
        "content_digest",
        "created_at",
        "legacy_sha256",
        "overlay",
        "schema_version",
        "source",
        "tenant_id",
        "updated_at",
    } or document.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise TenantSecurityAuthorityError("tenant policy document schema is invalid")
    if document.get("tenant_id") != tenant_id:
        raise TenantSecurityAuthorityError("tenant policy scope is inconsistent")
    overlay, actual_digest = validate_policy_overlay(document.get("overlay"))
    content_digest = _validate_digest(
        document.get("content_digest"),
        label="tenant policy digest",
    )
    if content_digest != actual_digest:
        raise TenantSecurityAuthorityError("tenant policy digest does not match payload")
    source = document.get("source")
    if source not in {"empty", "imported", "managed"}:
        raise TenantSecurityAuthorityError("tenant policy source is invalid")
    legacy_sha256 = document.get("legacy_sha256")
    if legacy_sha256 is not None:
        legacy_sha256 = _validate_digest(
            legacy_sha256,
            label="legacy tenant policy digest",
        )
    if source == "imported" and legacy_sha256 is None:
        raise TenantSecurityAuthorityError("imported tenant policy lacks provenance")
    created_at = _positive_time(document.get("created_at"), label="policy creation time")
    updated_at = _positive_time(document.get("updated_at"), label="policy update time")
    if updated_at < created_at:
        raise TenantSecurityAuthorityError("tenant policy update predates creation")
    _canonical_json(
        document,
        label="tenant policy document",
        max_bytes=_MAX_POLICY_BYTES + 4096,
    )
    return {
        "overlay": overlay,
        "content_digest": content_digest,
        "source": source,
        "legacy_sha256": legacy_sha256,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _legacy_policy_path(tenant_id: str) -> Path:
    return (
        maverick_home()
        / "tenants"
        / _tenant_segment(tenant_id)
        / "config.toml"
    )


def _legacy_wrapped_dek_path(tenant_id: str) -> Path:
    return (
        maverick_home()
        / "tenants"
        / _tenant_segment(tenant_id)
        / "keys"
        / "dek.wrapped"
    )


def _legacy_file(
    path: Path,
    *,
    label: str,
    max_bytes: int = _MAX_LEGACY_FILE_BYTES,
) -> tuple[bytes, str] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TenantSecurityAuthorityError(f"legacy {label} cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise TenantSecurityAuthorityError(f"legacy {label} is not a regular file")
    if info.st_size > max_bytes:
        raise TenantSecurityAuthorityError(f"legacy {label} exceeds its size bound")
    try:
        raw = atomic_read_bytes(path)
    except OSError as exc:
        raise TenantSecurityAuthorityError(f"legacy {label} cannot be read") from exc
    if len(raw) > max_bytes:
        raise TenantSecurityAuthorityError(f"legacy {label} exceeds its size bound")
    return raw, _digest(raw)


def _assert_legacy_binding(
    legacy: tuple[bytes, str] | None,
    *,
    bound_digest: str | None,
    label: str,
) -> None:
    if legacy is None:
        return
    if bound_digest != legacy[1]:
        raise TenantSecurityAuthorityError(
            f"legacy {label} exists; explicitly import it into governed Postgres "
            "and archive the local file before shared cutover"
        )


def load_policy_overlay(tenant_id: str) -> dict[str, Any]:
    """Read the exact tenant's governed policy overlay.

    An absent overlay becomes one durable empty policy record.  A local legacy
    file blocks this initialization unless its exact bytes were explicitly
    imported, preventing a replica from silently ignoring old policy.
    """
    tenant_id = _tenant_id(tenant_id)
    legacy = _legacy_file(_legacy_policy_path(tenant_id), label="tenant policy")
    store = _new_policy_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)
        document = store.read()
        policy = _validate_policy_document(
            document,
            tenant_id=tenant_id,
            initialize=False,
        )
        if policy is None:
            if legacy is not None:
                _assert_legacy_binding(
                    legacy,
                    bound_digest=None,
                    label="tenant policy",
                )

            def _initialize(current: dict[str, Any], now: float) -> None:
                if current:
                    _validate_policy_document(
                        current,
                        tenant_id=tenant_id,
                        initialize=False,
                    )
                    raise _NoAuthorityMutation(None)
                _validate_policy_document(
                    current,
                    tenant_id=tenant_id,
                    initialize=True,
                    now=now,
                )

            try:
                store.mutate_with_authoritative_time(
                    _initialize,
                    action="tenant_policy.initialize",
                    actor="tenant-policy-authority",
                )
            except _NoAuthorityMutation:
                pass
            document = store.read()
            policy = _validate_policy_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
        _require_postgres(store)
    if policy is None:  # pragma: no cover - store contract guard
        raise TenantSecurityAuthorityError("tenant policy disappeared after commit")
    _assert_legacy_binding(
        legacy,
        bound_digest=policy["legacy_sha256"],
        label="tenant policy",
    )
    # Detect a local rewrite that raced the shared read.
    after = _legacy_file(_legacy_policy_path(tenant_id), label="tenant policy")
    _assert_legacy_binding(
        after,
        bound_digest=policy["legacy_sha256"],
        label="tenant policy",
    )
    return copy.deepcopy(policy["overlay"])


def set_policy_overlay(
    tenant_id: str,
    overlay: dict[str, Any],
    *,
    expected_digest: str | None = None,
) -> str:
    """Replace governed tenant policy through CAS and return its digest."""
    tenant_id = _tenant_id(tenant_id)
    clean, content_digest = validate_policy_overlay(overlay)
    if expected_digest is not None:
        expected_digest = _validate_digest(
            expected_digest,
            label="expected tenant policy digest",
        )
    if _legacy_file(_legacy_policy_path(tenant_id), label="tenant policy") is not None:
        raise TenantSecurityAuthorityError(
            "archive the imported legacy tenant policy before managed updates"
        )
    store = _new_policy_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)

        def _apply(document: dict[str, Any], now: float) -> str:
            current = _validate_policy_document(
                document,
                tenant_id=tenant_id,
                initialize=True,
                now=now,
            )
            if current is None:  # pragma: no cover - initialized above
                raise TenantSecurityAuthorityError("tenant policy is unavailable")
            if (
                expected_digest is not None
                and current["content_digest"] != expected_digest
            ):
                raise TenantSecurityAuthorityError("tenant policy CAS digest changed")
            if current["content_digest"] == content_digest:
                raise _NoAuthorityMutation(content_digest)
            document["overlay"] = copy.deepcopy(clean)
            document["content_digest"] = content_digest
            document["source"] = "managed"
            document["legacy_sha256"] = None
            document["updated_at"] = now
            _validate_policy_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            return content_digest

        try:
            result = store.mutate_with_authoritative_time(
                _apply,
                action="tenant_policy.replace",
                actor="tenant-policy-authority",
            )
        except _NoAuthorityMutation as satisfied:
            result = satisfied.result
        _require_postgres(store)
    return result


def import_legacy_policy_overlay(tenant_id: str) -> str:
    """Explicitly import one raw tenant ``config.toml`` into shared policy."""
    tenant_id = _tenant_id(tenant_id)
    path = _legacy_policy_path(tenant_id)
    legacy = _legacy_file(path, label="tenant policy", max_bytes=_MAX_POLICY_BYTES)
    if legacy is None:
        raise TenantSecurityAuthorityError("legacy tenant policy does not exist")
    raw, legacy_digest = legacy
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TenantSecurityAuthorityError("legacy tenant policy is unreadable") from exc
    clean, content_digest = validate_policy_overlay(parsed)
    store = _new_policy_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)

        def _apply(document: dict[str, Any], now: float) -> str:
            current = _validate_policy_document(
                document,
                tenant_id=tenant_id,
                initialize=True,
                now=now,
            )
            if current is None:  # pragma: no cover
                raise TenantSecurityAuthorityError("tenant policy is unavailable")
            if current["content_digest"] not in {
                validate_policy_overlay({})[1],
                content_digest,
            }:
                raise TenantSecurityAuthorityError(
                    "governed tenant policy already contains different state"
                )
            if (
                current["content_digest"] == content_digest
                and current["source"] == "imported"
                and current["legacy_sha256"] == legacy_digest
            ):
                raise _NoAuthorityMutation(content_digest)
            document["overlay"] = copy.deepcopy(clean)
            document["content_digest"] = content_digest
            document["source"] = "imported"
            document["legacy_sha256"] = legacy_digest
            document["updated_at"] = now
            _validate_policy_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            return content_digest

        try:
            result = store.mutate_with_authoritative_time(
                _apply,
                action="tenant_policy.import",
                actor="tenant-policy-importer",
            )
        except _NoAuthorityMutation as satisfied:
            result = satisfied.result
        _require_postgres(store)
    after = _legacy_file(path, label="tenant policy", max_bytes=_MAX_POLICY_BYTES)
    if after is None or after[1] != legacy_digest:
        raise TenantSecurityAuthorityError(
            "legacy tenant policy changed during import; shared state is not admitted"
        )
    return result


def _validate_wrapped_dek_document(
    document: object,
    *,
    tenant_id: str,
    initialize: bool,
    now: float | None = None,
) -> tuple[WrappedDekRecord | None, str | None]:
    if not isinstance(document, dict):
        raise TenantSecurityAuthorityError("wrapped-DEK document is malformed")
    if not document:
        if not initialize:
            return None, None
        if now is None:
            raise TenantSecurityAuthorityError("wrapped-DEK timestamp is unavailable")
        document.update({
            "schema_version": WRAPPED_DEK_SCHEMA_VERSION,
            "tenant_id": tenant_id,
            "wrapped_dek": None,
            "legacy_sha256": None,
            "created_at": now,
            "updated_at": now,
        })
    if set(document) != {
        "created_at",
        "legacy_sha256",
        "schema_version",
        "tenant_id",
        "updated_at",
        "wrapped_dek",
    } or document.get("schema_version") != WRAPPED_DEK_SCHEMA_VERSION:
        raise TenantSecurityAuthorityError("wrapped-DEK document schema is invalid")
    if document.get("tenant_id") != tenant_id:
        raise TenantSecurityAuthorityError("wrapped-DEK tenant scope is inconsistent")
    created_at = _positive_time(document.get("created_at"), label="DEK creation time")
    updated_at = _positive_time(document.get("updated_at"), label="DEK update time")
    if updated_at < created_at:
        raise TenantSecurityAuthorityError("wrapped-DEK update predates creation")
    legacy_sha256 = document.get("legacy_sha256")
    if legacy_sha256 is not None:
        legacy_sha256 = _validate_digest(
            legacy_sha256,
            label="legacy wrapped-DEK digest",
        )
    raw_wrapper = document.get("wrapped_dek")
    if raw_wrapper is None:
        return None, legacy_sha256
    if not isinstance(raw_wrapper, dict) or set(raw_wrapper) != {
        "ciphertext",
        "ciphertext_sha256",
        "created_at",
        "generation",
        "policy_digest",
        "rotated_at",
    }:
        raise TenantSecurityAuthorityError("wrapped-DEK payload schema is invalid")
    encoded = raw_wrapper.get("ciphertext")
    if not isinstance(encoded, str) or len(encoded) > (_MAX_WRAPPED_DEK_BYTES * 2):
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext is invalid")
    try:
        ciphertext = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext is invalid") from exc
    if not ciphertext or len(ciphertext) > _MAX_WRAPPED_DEK_BYTES:
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext size is invalid")
    ciphertext_sha256 = _validate_digest(
        raw_wrapper.get("ciphertext_sha256"),
        label="wrapped-DEK ciphertext digest",
    )
    if ciphertext_sha256 != _digest(ciphertext):
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext digest mismatches")
    policy_digest = _validate_digest(
        raw_wrapper.get("policy_digest"),
        label="wrapped-DEK policy digest",
    )
    generation = raw_wrapper.get("generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or generation > 2**63 - 1
    ):
        raise TenantSecurityAuthorityError("wrapped-DEK generation is invalid")
    wrapper_created = _positive_time(
        raw_wrapper.get("created_at"),
        label="wrapped-DEK creation time",
    )
    rotated_raw = raw_wrapper.get("rotated_at")
    rotated_at = (
        None
        if rotated_raw is None
        else _positive_time(rotated_raw, label="wrapped-DEK rotation time")
    )
    if rotated_at is not None and rotated_at < wrapper_created:
        raise TenantSecurityAuthorityError("wrapped-DEK rotation predates creation")
    _canonical_json(
        document,
        label="wrapped-DEK document",
        max_bytes=_MAX_WRAPPED_DEK_BYTES * 2 + 4096,
    )
    return (
        WrappedDekRecord(
            ciphertext=ciphertext,
            ciphertext_sha256=ciphertext_sha256,
            policy_digest=policy_digest,
            generation=generation,
            created_at=wrapper_created,
            rotated_at=rotated_at,
        ),
        legacy_sha256,
    )


def _read_wrapped_document(
    tenant_id: str,
) -> tuple[WrappedDekRecord | None, str | None]:
    store = _new_wrapped_dek_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)
        result = _validate_wrapped_dek_document(
            store.read(),
            tenant_id=tenant_id,
            initialize=False,
        )
        _require_postgres(store)
    return result


def read_wrapped_dek(tenant_id: str) -> WrappedDekRecord | None:
    tenant_id = _tenant_id(tenant_id)
    wrapper, legacy_digest = _read_wrapped_document(tenant_id)
    legacy = _legacy_file(
        _legacy_wrapped_dek_path(tenant_id),
        label="wrapped tenant DEK",
        max_bytes=_MAX_WRAPPED_DEK_BYTES,
    )
    _assert_legacy_binding(
        legacy,
        bound_digest=legacy_digest,
        label="wrapped tenant DEK",
    )
    return wrapper


def authoritative_time(tenant_id: str) -> float:
    tenant_id = _tenant_id(tenant_id)
    store = _new_wrapped_dek_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)
        now = _positive_time(
            store.authoritative_time(),
            label="shared tenant authority time",
        )
        _require_postgres(store)
    return now


def _wrapper_payload(
    ciphertext: bytes,
    *,
    policy_digest: str,
    generation: int,
    created_at: float,
    rotated_at: float | None,
) -> dict[str, Any]:
    if not isinstance(ciphertext, bytes) or not ciphertext:
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext is required")
    if len(ciphertext) > _MAX_WRAPPED_DEK_BYTES:
        raise TenantSecurityAuthorityError("wrapped-DEK ciphertext exceeds its bound")
    policy_digest = _validate_digest(policy_digest, label="wrapped-DEK policy digest")
    return {
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "ciphertext_sha256": _digest(ciphertext),
        "policy_digest": policy_digest,
        "generation": generation,
        "created_at": created_at,
        "rotated_at": rotated_at,
    }


def create_wrapped_dek(
    tenant_id: str,
    ciphertext: bytes,
    *,
    policy_digest: str,
    legacy_sha256: str | None = None,
) -> WrappedDekRecord:
    """Create-once wrapped DEK; concurrent creators adopt the CAS winner."""
    tenant_id = _tenant_id(tenant_id)
    policy_digest = _validate_digest(policy_digest, label="wrapped-DEK policy digest")
    if legacy_sha256 is not None:
        legacy_sha256 = _validate_digest(
            legacy_sha256,
            label="legacy wrapped-DEK digest",
        )
    legacy = _legacy_file(
        _legacy_wrapped_dek_path(tenant_id),
        label="wrapped tenant DEK",
        max_bytes=_MAX_WRAPPED_DEK_BYTES,
    )
    _assert_legacy_binding(
        legacy,
        bound_digest=legacy_sha256,
        label="wrapped tenant DEK",
    )
    store = _new_wrapped_dek_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)

        def _apply(document: dict[str, Any], now: float) -> WrappedDekRecord:
            current, bound_legacy = _validate_wrapped_dek_document(
                document,
                tenant_id=tenant_id,
                initialize=True,
                now=now,
            )
            if current is not None:
                if legacy_sha256 is not None and bound_legacy not in {
                    None,
                    legacy_sha256,
                }:
                    raise TenantSecurityAuthorityError(
                        "wrapped-DEK import provenance conflicts"
                    )
                if legacy_sha256 is not None and bound_legacy is None:
                    if current.ciphertext_sha256 != legacy_sha256:
                        raise TenantSecurityAuthorityError(
                            "governed wrapped DEK differs from legacy state"
                        )
                    document["legacy_sha256"] = legacy_sha256
                    document["updated_at"] = now
                    return current
                raise _NoAuthorityMutation(current)
            payload = _wrapper_payload(
                ciphertext,
                policy_digest=policy_digest,
                generation=1,
                created_at=now,
                rotated_at=None,
            )
            document["wrapped_dek"] = payload
            document["legacy_sha256"] = legacy_sha256
            document["updated_at"] = now
            saved, _legacy = _validate_wrapped_dek_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            if saved is None:  # pragma: no cover
                raise TenantSecurityAuthorityError("wrapped DEK was not persisted")
            return saved

        try:
            result = store.mutate_with_authoritative_time(
                _apply,
                action="tenant_dek.create",
                actor="tenant-key-authority",
            )
        except _NoAuthorityMutation as satisfied:
            result = satisfied.result
        _require_postgres(store)
    return result


def replace_wrapped_dek(
    tenant_id: str,
    ciphertext: bytes,
    *,
    policy_digest: str,
    expected_ciphertext_sha256: str,
    expected_generation: int,
) -> WrappedDekRecord:
    """CAS-replace one wrapped DEK after KMS effects were prepared externally."""
    tenant_id = _tenant_id(tenant_id)
    policy_digest = _validate_digest(policy_digest, label="wrapped-DEK policy digest")
    expected_ciphertext_sha256 = _validate_digest(
        expected_ciphertext_sha256,
        label="expected wrapped-DEK digest",
    )
    if (
        not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
        or expected_generation < 1
    ):
        raise TenantSecurityAuthorityError("expected wrapped-DEK generation is invalid")
    current, legacy_digest = _read_wrapped_document(tenant_id)
    legacy = _legacy_file(
        _legacy_wrapped_dek_path(tenant_id),
        label="wrapped tenant DEK",
        max_bytes=_MAX_WRAPPED_DEK_BYTES,
    )
    _assert_legacy_binding(
        legacy,
        bound_digest=legacy_digest,
        label="wrapped tenant DEK",
    )
    if current is None:
        raise TenantSecurityAuthorityError("no wrapped DEK exists for this tenant")
    store = _new_wrapped_dek_store(tenant_id)
    with _bootstrap_scope(tenant_id):
        _require_postgres(store)

        def _apply(document: dict[str, Any], now: float) -> WrappedDekRecord:
            existing, _legacy = _validate_wrapped_dek_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            if existing is None:
                raise WrappedDekConflict("wrapped DEK disappeared during rotation")
            if (
                existing.ciphertext_sha256 != expected_ciphertext_sha256
                or existing.generation != expected_generation
            ):
                raise WrappedDekConflict("wrapped DEK changed during rotation")
            payload = _wrapper_payload(
                ciphertext,
                policy_digest=policy_digest,
                generation=existing.generation + 1,
                created_at=existing.created_at,
                rotated_at=now,
            )
            document["wrapped_dek"] = payload
            document["updated_at"] = now
            saved, _bound = _validate_wrapped_dek_document(
                document,
                tenant_id=tenant_id,
                initialize=False,
            )
            if saved is None:  # pragma: no cover
                raise TenantSecurityAuthorityError("wrapped DEK was not replaced")
            return saved

        result = store.mutate_with_authoritative_time(
            _apply,
            action="tenant_dek.rotate",
            actor="tenant-key-authority",
        )
        _require_postgres(store)
    return result


def import_legacy_wrapped_dek(
    tenant_id: str,
    *,
    policy_digest: str,
) -> WrappedDekRecord:
    """Explicitly import the exact legacy wrapped-DEK ciphertext."""
    tenant_id = _tenant_id(tenant_id)
    legacy = _legacy_file(
        _legacy_wrapped_dek_path(tenant_id),
        label="wrapped tenant DEK",
        max_bytes=_MAX_WRAPPED_DEK_BYTES,
    )
    if legacy is None:
        raise TenantSecurityAuthorityError("legacy wrapped tenant DEK does not exist")
    raw, legacy_digest = legacy
    result = create_wrapped_dek(
        tenant_id,
        raw,
        policy_digest=policy_digest,
        legacy_sha256=legacy_digest,
    )
    after = _legacy_file(
        _legacy_wrapped_dek_path(tenant_id),
        label="wrapped tenant DEK",
        max_bytes=_MAX_WRAPPED_DEK_BYTES,
    )
    if after is None or after[1] != legacy_digest:
        raise TenantSecurityAuthorityError(
            "legacy wrapped tenant DEK changed during import"
        )
    return result


__all__ = [
    "TenantSecurityAuthorityError",
    "WrappedDekConflict",
    "WrappedDekRecord",
    "authoritative_time",
    "create_wrapped_dek",
    "import_legacy_policy_overlay",
    "import_legacy_wrapped_dek",
    "load_policy_overlay",
    "read_wrapped_dek",
    "replace_wrapped_dek",
    "set_policy_overlay",
    "shared_security_authority_enabled",
    "tenant_authority_scope",
    "validate_policy_overlay",
]
