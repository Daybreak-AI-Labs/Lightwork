"""Single policy-aware construction boundary for the knowledge plane."""
from __future__ import annotations

import getpass
import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)


class KnowledgeFactoryError(RuntimeError):
    """The configured knowledge plane could not meet its runtime contract."""


def _authenticated_principal() -> str:
    try:
        from .connections import current_principal

        return str(current_principal() or "").strip()
    except Exception:
        return ""


def _local_runtime_principal() -> str:
    principal = _authenticated_principal()
    if principal:
        return principal
    try:
        return f"os:{getpass.getuser()}"
    except Exception:  # pragma: no cover - exotic embedded runtimes
        return ""


def _tenant_bound_knowledge_config(
    cfg: dict,
    *,
    path_was_configured: bool,
) -> dict:
    """Bind every knowledge backend to the strict active tenant identity.

    A configured SQLite path and a shared pgvector/Qdrant DSN are deployment
    locations, not tenant identities.  Derive a collision-resistant local path
    for configured locations and inject an explicit namespace independently of
    either value.  The private tenant fields also let key custody require an
    explicit per-tenant template instead of silently reusing one HMAC key.
    """
    from .paths import current_tenant_id_strict, current_tenant_strict

    tenant_id = current_tenant_id_strict()
    tenant_segment = current_tenant_strict()
    bound = dict(cfg)
    if not tenant_id and not tenant_segment:
        return bound
    if not tenant_id or not tenant_segment:
        raise KnowledgeFactoryError("knowledge tenant identity is inconsistent")

    tenant_hash = hashlib.sha256(
        ("maverick-knowledge-tenant-v1\0" + tenant_segment).encode("ascii")
    ).hexdigest()
    bound["namespace"] = f"tenant:{tenant_hash}"
    bound["_tenant_segment"] = tenant_segment
    bound["_tenant_hash"] = tenant_hash

    raw_path = str(bound.get("path") or "")
    if path_was_configured and raw_path and raw_path != ":memory:":
        configured = Path(raw_path).expanduser()
        if not configured.name:
            raise KnowledgeFactoryError(
                "configured knowledge path must identify a database file"
            )
        # A full SHA-256 directory avoids portable-filesystem aliases even
        # when the configured root is outside MAVERICK_HOME.  If an operator
        # already supplied this exact derived path, keep it stable.
        tenant_dir = f"tenant-{tenant_hash}"
        if not (
            configured.parent.name == tenant_dir
            and configured.parent.parent.name == "tenants"
        ):
            configured = configured.parent / "tenants" / tenant_dir / configured.name
        bound["path"] = str(configured)
    return bound


def _expand_tenant_key_path(
    raw_path: str,
    *,
    tenant_segment: str,
    tenant_hash: str,
    label: str,
) -> str:
    """Resolve an explicitly tenant-keyed custody path or fail closed."""
    value = str(raw_path or "").strip()
    if not tenant_segment:
        return value
    if "{tenant}" not in value and "{tenant_hash}" not in value:
        raise KnowledgeFactoryError(
            f"tenant-scoped knowledge {label} must use a {{tenant}} or "
            "{tenant_hash} path template"
        )
    expanded = value.replace("{tenant}", tenant_segment).replace(
        "{tenant_hash}", tenant_hash,
    )
    if "{" in expanded or "}" in expanded:
        raise KnowledgeFactoryError(
            f"tenant-scoped knowledge {label} contains an unsupported template"
        )
    return expanded


def _quarantine_path(knowledge_path: str) -> Path:
    path = Path(str(knowledge_path)).expanduser()
    if str(knowledge_path) == ":memory:":
        from .workspace import Workspace

        path = Workspace.current().knowledge_path
    return path.with_name(path.name + ".quarantine.sqlite3")


def _validate_required_transports(cfg: dict) -> None:
    """Refuse production knowledge credentials/content over plaintext links."""
    provider = str(
        os.environ.get("MAVERICK_EMBED_PROVIDER")
        or cfg.get("embedder")
        or "hosted"
    ).lower()
    if provider in {"hosted", "cohere"}:
        endpoint = str(cfg.get("base_url") or "").strip()
        if urlsplit(endpoint).scheme.lower() != "https":
            raise KnowledgeFactoryError(
                "required hosted knowledge embeddings need an HTTPS endpoint"
            )

    backend = str(cfg.get("store") or "sqlite").lower()
    if backend == "qdrant":
        endpoint = str(
            cfg.get("url") or os.environ.get("QDRANT_URL") or ""
        ).strip()
        if urlsplit(endpoint).scheme.lower() != "https":
            raise KnowledgeFactoryError(
                "required Qdrant knowledge needs an HTTPS endpoint"
            )
    if backend == "pgvector":
        dsn = str(
            cfg.get("dsn")
            or os.environ.get("MAVERICK_KNOWLEDGE_DSN")
            or os.environ.get("MAVERICK_PG_DSN")
            or ""
        ).strip()
        sslmode = ""
        if "://" in dsn:
            values = parse_qs(urlsplit(dsn).query).get("sslmode", [])
            sslmode = str(values[-1]).lower() if values else ""
        else:
            for token in dsn.split():
                if token.lower().startswith("sslmode="):
                    sslmode = token.split("=", 1)[1].strip().lower()
        if sslmode not in {"require", "verify-ca", "verify-full"}:
            raise KnowledgeFactoryError(
                "required pgvector knowledge needs sslmode=require, "
                "verify-ca, or verify-full"
            )


def _integrity_key_path(knowledge_path: str) -> Path:
    path = Path(str(knowledge_path)).expanduser()
    if str(knowledge_path) == ":memory:":
        from .workspace import Workspace

        path = Workspace.current().knowledge_path
    return path.with_name(path.name + ".integrity.key")


def _quarantine_head_path(cfg: dict, *, required: bool) -> Path | None:
    configured = (
        os.environ.get("MAVERICK_KNOWLEDGE_QUARANTINE_HEAD_FILE", "").strip()
        or str(cfg.get("quarantine_head_file") or "").strip()
    )
    if not configured:
        if required:
            raise KnowledgeFactoryError(
                "required knowledge mode needs quarantine_head_file in "
                "external custody"
            )
        return None
    expanded = _expand_tenant_key_path(
        configured,
        tenant_segment=str(cfg.get("_tenant_segment") or ""),
        tenant_hash=str(cfg.get("_tenant_hash") or ""),
        label="quarantine_head_file",
    )
    head_path = Path(expanded).expanduser()
    if required:
        db_parent = Path(str(cfg["path"])).expanduser().resolve(
            strict=False,
        ).parent
        quarantine_parent = _quarantine_path(str(cfg["path"])).resolve(
            strict=False,
        ).parent
        head_parent = head_path.resolve(strict=False).parent
        if head_parent in {db_parent, quarantine_parent}:
            raise KnowledgeFactoryError(
                "required knowledge quarantine head must not be colocated "
                "with the database or quarantine ledger"
            )
    return head_path


def _decode_integrity_key(raw: bytes) -> bytes:
    if len(raw) == 32:
        return raw
    value = raw.strip()
    if len(value) == 64:
        try:
            decoded = bytes.fromhex(value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            decoded = b""
        if len(decoded) == 32:
            return decoded
    raise KnowledgeFactoryError(
        "knowledge record-integrity key must contain 32 raw bytes or 64 hex characters"
    )


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _load_integrity_key(
    path: Path,
    *,
    required: bool,
    allow_create: bool = False,
) -> bytes:
    from .file_lock import harden_path_permissions, read_custodied_bytes

    try:
        raw = read_custodied_bytes(
            path,
            custody_root=path.parent,
            max_bytes=128,
            repair_permissions=not required,
            require_private_root=required,
        )
        return _decode_integrity_key(raw)
    except FileNotFoundError:
        if required or not allow_create:
            raise KnowledgeFactoryError(
                f"required knowledge mode needs a pre-provisioned integrity key at {path}"
            ) from None
    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _load_integrity_key(path, required=required, allow_create=False)
    with os.fdopen(fd, "wb") as handle:
        handle.write(generated)
        handle.flush()
        os.fsync(handle.fileno())
    harden_path_permissions(path, 0o600)
    return _load_integrity_key(path, required=False, allow_create=False)


def _previous_key_paths(raw: object) -> list[Path]:
    if raw in (None, "", []):
        return []
    values: list[object]
    if isinstance(raw, str):
        values = [value for value in raw.split(os.pathsep) if value]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        raise KnowledgeFactoryError(
            "knowledge integrity_previous_key_files must be a list of paths"
        )
    paths = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise KnowledgeFactoryError(
                "knowledge integrity_previous_key_files contains an invalid path"
            )
        paths.append(Path(value).expanduser())
    return paths


def _load_integrity_keyring(
    cfg: dict,
    *,
    required: bool,
) -> tuple[bytes, dict[str, bytes], str]:
    knowledge_path = str(cfg["path"])
    configured = (
        os.environ.get("MAVERICK_KNOWLEDGE_INTEGRITY_KEY_FILE", "").strip()
        or str(cfg.get("integrity_key_file") or "").strip()
    )
    tenant_segment = str(cfg.get("_tenant_segment") or "")
    tenant_hash = str(cfg.get("_tenant_hash") or "")
    if configured:
        configured = _expand_tenant_key_path(
            configured,
            tenant_segment=tenant_segment,
            tenant_hash=tenant_hash,
            label="integrity_key_file",
        )
    if required and not configured:
        raise KnowledgeFactoryError(
            "required knowledge mode needs integrity_key_file in external custody"
        )
    active_path = (
        Path(configured).expanduser()
        if configured
        else _integrity_key_path(knowledge_path)
    )
    db_path = Path(knowledge_path).expanduser()
    if required and active_path.resolve(strict=False).parent == db_path.resolve(
        strict=False,
    ).parent:
        raise KnowledgeFactoryError(
            "required knowledge integrity key must not be colocated with the database"
        )
    active = _load_integrity_key(
        active_path,
        required=required,
        allow_create=not required and not configured,
    )
    previous_raw = os.environ.get(
        "MAVERICK_KNOWLEDGE_INTEGRITY_PREVIOUS_KEY_FILES",
    )
    if previous_raw is None:
        previous_raw = cfg.get("integrity_previous_key_files", [])
    keys = {_key_id(active): active}
    for raw_path in _previous_key_paths(previous_raw):
        expanded = _expand_tenant_key_path(
            str(raw_path),
            tenant_segment=tenant_segment,
            tenant_hash=tenant_hash,
            label="integrity_previous_key_files",
        )
        path = Path(expanded).expanduser()
        if required and path.resolve(strict=False).parent == db_path.resolve(
            strict=False,
        ).parent:
            raise KnowledgeFactoryError(
                "required previous integrity keys must not be colocated with the database"
            )
        key = _load_integrity_key(path, required=required, allow_create=False)
        keys[_key_id(key)] = key
    active_id = _key_id(active)
    return active, keys, active_id


def open_configured_knowledge_base(
    *,
    tenant: str | None = None,
    required_mode: bool | None = None,
) -> Any | None:
    """Build the one supported KnowledgeBase composition.

    Development remains optional and fail-soft.  An explicitly admitted
    production process uses required mode: Shield, OCR/describer, persistent
    quarantine, provenance attribution, and every store/embedder dependency
    must construct successfully or startup refuses the knowledge surface.

    An explicit ``tenant`` scopes config, paths, store namespace, quarantine,
    and key selection for the entire construction operation.
    """
    from .paths import tenant_scope

    with tenant_scope(tenant=tenant):
        return _open_current_knowledge_base(required_mode=required_mode)


def _open_current_knowledge_base(
    *,
    required_mode: bool | None,
) -> Any | None:
    from .production_exclusions import feature_allowed

    if not feature_allowed("knowledge"):
        return None
    from .config import get_knowledge

    kcfg = dict(get_knowledge())
    if not kcfg.get("enable"):
        return None
    from .production_scope import production_surface_required

    required = bool(production_surface_required() or bool(required_mode))
    store = None
    quarantine = None
    try:
        path_was_configured = bool(kcfg.get("path"))
        if not path_was_configured:
            from .workspace import Workspace

            kcfg = {**kcfg, "path": str(Workspace.current().knowledge_path)}
        kcfg = _tenant_bound_knowledge_config(
            kcfg,
            path_was_configured=path_was_configured,
        )
        from maverick_knowledge import (
            DeterministicEmbedder,
            KnowledgeBase,
            SqliteQuarantineLedger,
            build_embedder,
            build_store,
        )
        if required:
            _validate_required_transports(kcfg)

        shield = None
        try:
            from maverick_shield import Shield

            shield = Shield.from_config(warn_if_missing=False)
            if required and (
                getattr(shield, "enabled", True) is False
                or getattr(shield, "backend", "") == "none"
                or getattr(shield, "_scan_output_enabled", True) is False
            ):
                raise KnowledgeFactoryError(
                    "required knowledge mode needs an active Shield policy"
                )
        except Exception as exc:
            if required:
                raise KnowledgeFactoryError(
                    "required knowledge mode could not construct the active Shield"
                ) from exc
            log.warning("knowledge factory: optional Shield unavailable: %s", exc)

        image_describer = None
        try:
            from maverick_knowledge.image import build_ocr_describer

            image_describer = build_ocr_describer()
        except Exception as exc:
            if required:
                raise KnowledgeFactoryError(
                    "required knowledge mode could not construct the image/OCR describer"
                ) from exc
            log.info("knowledge factory: optional image/OCR describer unavailable: %s", exc)

        backend = _bounded_policy_component(getattr(shield, "backend", "none"))
        profile = _bounded_policy_component(getattr(shield, "profile", "default"))
        policy_id = f"knowledge-ingest-v1:{profile}:{backend}"
        integrity_key, integrity_keys, active_key_id = _load_integrity_keyring(
            kcfg,
            required=required,
        )
        configured_backend = str(kcfg.get("store") or "sqlite").lower()
        if configured_backend not in {"sqlite", "pgvector", "qdrant"}:
            raise KnowledgeFactoryError(
                f"unknown configured knowledge store {configured_backend!r}"
            )
        single_writer = bool(kcfg.get("single_writer", False))
        if required and configured_backend == "pgvector" and not single_writer:
            raise KnowledgeFactoryError(
                "required pgvector knowledge needs a shared quarantine ledger or "
                "an explicit [knowledge] single_writer = true deployment contract"
            )
        quarantine_head = _quarantine_head_path(kcfg, required=required)
        quarantine = SqliteQuarantineLedger(
            _quarantine_path(str(kcfg["path"])),
            integrity_key=integrity_key,
            integrity_keys=integrity_keys,
            active_integrity_key_id=active_key_id,
            required=required,
            single_writer_contract=single_writer,
            head_path=quarantine_head,
        )
        kcfg["_required_mode"] = required
        store = build_store(kcfg)
        if getattr(store, "backend_name", "") != configured_backend:
            raise KnowledgeFactoryError(
                "knowledge store implementation does not match configured backend"
            )
        embedder = build_embedder(kcfg)
        configured_provider = str(
            os.environ.get("MAVERICK_EMBED_PROVIDER")
            or kcfg.get("embedder")
            or "hosted"
        ).lower()
        if getattr(embedder, "provider_name", "") != configured_provider:
            raise KnowledgeFactoryError(
                "knowledge embedder implementation does not match configured provider"
            )
        if required and isinstance(embedder, DeterministicEmbedder):
            raise KnowledgeFactoryError(
                "required knowledge mode refuses the non-semantic deterministic "
                "embedder"
            )
        return KnowledgeBase(
            store=store,
            embedder=embedder,
            shield=shield,
            image_describer=image_describer,
            required_mode=required,
            quarantine=quarantine,
            policy_id=policy_id,
            principal_resolver=(
                _authenticated_principal if required else _local_runtime_principal
            ),
            integrity_key=integrity_key,
            integrity_keys=integrity_keys,
            active_integrity_key_id=active_key_id,
        )
    except KnowledgeFactoryError:
        _close_factory_resource(store)
        _close_factory_resource(quarantine)
        raise
    except Exception as exc:
        _close_factory_resource(store)
        _close_factory_resource(quarantine)
        if required:
            raise KnowledgeFactoryError(
                "required knowledge mode could not satisfy its construction contract"
            ) from exc
        log.warning("knowledge base unavailable (fail-open): %s", exc)
        return None


def _close_factory_resource(resource: object) -> None:
    closer = getattr(resource, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _bounded_policy_component(value: object) -> str:
    text = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in str(value or "unknown")
    )
    return text[:64] or "unknown"


__all__ = [
    "KnowledgeFactoryError",
    "open_configured_knowledge_base",
]
