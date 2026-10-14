"""Deployment-bound production model and provider admission policy.

The production surface names an admitted provider family, but a provider name
is not enough authority to send customer data.  This module binds every live
dispatch to one reviewed model artifact, endpoint, region, retention posture,
credential source, spend ceiling, and failover chain.  Development processes
remain unchanged; an explicitly admitted production artifact fails closed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA = "lightwork.production-model-policy.v1"
POLICY_ENV = "MAVERICK_PRODUCTION_MODEL_POLICY"
POLICY_SHA256_ENV = "MAVERICK_PRODUCTION_MODEL_POLICY_SHA256"
ARTIFACT_DIGEST_ENV = "MAVERICK_MODEL_ARTIFACT_SHA256"
REGION_ENV = "MAVERICK_MODEL_REGION"
RETENTION_ENV = "MAVERICK_MODEL_RETENTION_MODE"
CREDENTIAL_SOURCE_ENV = "MAVERICK_MODEL_CREDENTIAL_SOURCE"
PRODUCTION_SURFACE_DIGEST_MODE = "utf8-lf-sha256"

_MAX_POLICY_BYTES = 512 * 1024
_MAX_SURFACE_BYTES = 1024 * 1024
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_REGION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_STATUS = frozenset({"draft", "approved"})
_RETENTION_MODES = frozenset(
    {
        "self-hosted-no-provider-retention",
        "zero-data-retention",
        "contract-bound-retention",
    }
)
_CREDENTIAL_SOURCES = frozenset(
    {"mounted_file", "workload_identity", "mutual_tls"}
)
_PERIODS = frozenset({"day", "month"})
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "policy_id",
        "profile_id",
        "source_version",
        "production_surface_sha256",
        "production_surface_digest_mode",
        "status",
        "approved_at",
        "expires_at",
        "approvers",
        "models",
        "limitations",
    }
)
_MODEL_KEYS = frozenset(
    {
        "spec",
        "artifact_digest",
        "base_url",
        "region",
        "retention_mode",
        "credential_source",
        "max_spend_usd",
        "spend_period",
        "failover",
    }
)
_APPROVER_KEYS = frozenset({"principal", "role"})


class ProductionModelPolicyError(PermissionError):
    """The production model policy is absent, invalid, stale, or violated."""


@dataclass(frozen=True)
class ProductionModel:
    spec: str
    provider: str
    model_id: str
    artifact_digest: str
    base_url: str
    region: str
    retention_mode: str
    credential_source: str
    max_spend_usd: float
    spend_period: str
    failover: tuple[str, ...]


@dataclass(frozen=True)
class ProductionModelPolicy:
    path: Path | None
    sha256: str | None
    policy_id: str
    profile_id: str
    source_version: str
    production_surface_sha256: str
    production_surface_digest_mode: str
    status: str
    approved_at: dt.datetime | None
    expires_at: dt.datetime | None
    approvers: tuple[tuple[str, str], ...]
    models: dict[str, ProductionModel]
    limitations: tuple[str, ...]


def _exact_mapping(
    value: object,
    keys: frozenset[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ProductionModelPolicyError(f"{label} fields are not the exact schema")
    return value


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProductionModelPolicyError(f"{label} must be a trimmed non-empty string")
    return value


def _timestamp(value: object, *, label: str) -> dt.datetime:
    text = _text(value, label=label)
    if not text.endswith("Z"):
        raise ProductionModelPolicyError(f"{label} must be an exact UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise ProductionModelPolicyError(
            f"{label} must be an exact UTC timestamp"
        ) from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise ProductionModelPolicyError(f"{label} must be an exact UTC timestamp")
    return parsed


def _canonical_spec(spec: object) -> tuple[str, str, str]:
    text = _text(spec, label="model spec")
    provider, separator, model_id = text.partition(":")
    if separator != ":" or not provider or not model_id:
        raise ProductionModelPolicyError(
            "production model specs must be provider-qualified"
        )
    from .providers import _canonical

    canonical_provider = _canonical(provider)
    canonical = f"{canonical_provider}:{model_id}"
    if canonical != text:
        raise ProductionModelPolicyError(
            f"production model spec must use canonical spelling: {canonical}"
        )
    return canonical, canonical_provider, model_id


def _base_url(value: object) -> str:
    text = _text(value, label="model base_url")
    parsed = urlparse(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProductionModelPolicyError("model base_url has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProductionModelPolicyError(
            "model base_url must be credential-free HTTPS without query or fragment"
        )
    host = parsed.hostname.lower().rstrip(".")
    if not host or any(char.isspace() for char in host):
        raise ProductionModelPolicyError("model base_url host is invalid")
    authority = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    normalized = f"https://{authority}{path}"
    if text != normalized:
        raise ProductionModelPolicyError(
            f"model base_url must use canonical spelling: {normalized}"
        )
    return normalized


def _positive_money(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionModelPolicyError("max_spend_usd must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0 or result > 1_000_000_000:
        raise ProductionModelPolicyError(
            "max_spend_usd must be positive, finite, and bounded"
        )
    return result


def production_surface_sha256(raw: bytes) -> str:
    """Hash production-surface UTF-8 text after canonical LF normalization."""
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProductionModelPolicyError(
            "production surface manifest is not valid UTF-8"
        ) from exc
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_policy_document(  # noqa: C901 - exact cross-field authority gate
    document: object,
    *,
    production_surface_sha256: str,
    require_approved: bool = False,
    now: dt.datetime | None = None,
    path: Path | None = None,
    sha256: str | None = None,
) -> ProductionModelPolicy:
    root = _exact_mapping(document, _TOP_LEVEL_KEYS, label="model policy")
    if root["schema"] != SCHEMA:
        raise ProductionModelPolicyError("production model policy schema is unsupported")
    policy_id = _text(root["policy_id"], label="policy_id")
    profile_id = _text(root["profile_id"], label="profile_id")
    source_version = _text(root["source_version"], label="source_version")
    surface_digest = _text(
        root["production_surface_sha256"],
        label="production_surface_sha256",
    )
    surface_digest_mode = _text(
        root["production_surface_digest_mode"],
        label="production_surface_digest_mode",
    )
    if surface_digest_mode != PRODUCTION_SURFACE_DIGEST_MODE:
        raise ProductionModelPolicyError(
            "production_surface_digest_mode is unsupported"
        )
    if _HEX_SHA256.fullmatch(surface_digest) is None:
        raise ProductionModelPolicyError(
            "production_surface_sha256 must be lowercase SHA-256"
        )
    if surface_digest != production_surface_sha256:
        raise ProductionModelPolicyError(
            "production model policy targets different canonical surface content"
        )
    status = root["status"]
    if status not in _STATUS:
        raise ProductionModelPolicyError("production model policy status is invalid")

    raw_models = root["models"]
    if not isinstance(raw_models, list):
        raise ProductionModelPolicyError("production models must be an array")
    models: dict[str, ProductionModel] = {}
    provider_contracts: dict[str, tuple[str, str]] = {}
    for index, raw_model in enumerate(raw_models):
        item = _exact_mapping(raw_model, _MODEL_KEYS, label=f"model {index}")
        spec, provider, model_id = _canonical_spec(item["spec"])
        if spec in models:
            raise ProductionModelPolicyError(f"duplicate production model: {spec}")
        artifact_digest = _text(
            item["artifact_digest"], label=f"{spec} artifact_digest"
        )
        if _SHA256.fullmatch(artifact_digest) is None:
            raise ProductionModelPolicyError(
                f"{spec} artifact_digest must be sha256:<lowercase hex>"
            )
        base_url = _base_url(item["base_url"])
        region = _text(item["region"], label=f"{spec} region")
        if _REGION.fullmatch(region) is None:
            raise ProductionModelPolicyError(f"{spec} region is invalid")
        retention_mode = item["retention_mode"]
        if retention_mode not in _RETENTION_MODES:
            raise ProductionModelPolicyError(f"{spec} retention_mode is invalid")
        credential_source = item["credential_source"]
        if credential_source not in _CREDENTIAL_SOURCES:
            raise ProductionModelPolicyError(f"{spec} credential_source is invalid")
        spend_period = item["spend_period"]
        if spend_period not in _PERIODS:
            raise ProductionModelPolicyError(f"{spec} spend_period is invalid")
        failover_raw = item["failover"]
        if not isinstance(failover_raw, list):
            raise ProductionModelPolicyError(f"{spec} failover must be an array")
        failover: list[str] = []
        for candidate in failover_raw:
            canonical, _, _ = _canonical_spec(candidate)
            if canonical == spec or canonical in failover:
                raise ProductionModelPolicyError(
                    f"{spec} failover contains self or duplicate destination"
                )
            failover.append(canonical)
        provider_contract = (base_url, credential_source)
        if provider in provider_contracts and provider_contracts[provider] != (
            provider_contract
        ):
            raise ProductionModelPolicyError(
                f"provider {provider} has conflicting endpoint or credential policy"
            )
        provider_contracts[provider] = provider_contract
        models[spec] = ProductionModel(
            spec=spec,
            provider=provider,
            model_id=model_id,
            artifact_digest=artifact_digest,
            base_url=base_url,
            region=region,
            retention_mode=retention_mode,
            credential_source=credential_source,
            max_spend_usd=_positive_money(item["max_spend_usd"]),
            spend_period=spend_period,
            failover=tuple(failover),
        )

    limitations_raw = root["limitations"]
    if not isinstance(limitations_raw, list) or not limitations_raw:
        raise ProductionModelPolicyError("production model limitations are required")
    limitations = tuple(
        _text(value, label="model policy limitation") for value in limitations_raw
    )

    approved_at: dt.datetime | None = None
    expires_at: dt.datetime | None = None
    approvers: list[tuple[str, str]] = []
    raw_approvers = root["approvers"]
    if not isinstance(raw_approvers, list):
        raise ProductionModelPolicyError("model policy approvers must be an array")
    if status == "draft":
        if root["approved_at"] is not None or root["expires_at"] is not None:
            raise ProductionModelPolicyError("draft model policy cannot carry dates")
        if raw_approvers:
            raise ProductionModelPolicyError("draft model policy cannot carry approvers")
    else:
        if not models:
            raise ProductionModelPolicyError("approved model policy needs a model")
        approved_at = _timestamp(root["approved_at"], label="approved_at")
        expires_at = _timestamp(root["expires_at"], label="expires_at")
        if (
            expires_at <= approved_at
            or expires_at - approved_at > dt.timedelta(days=366)
        ):
            raise ProductionModelPolicyError("model policy approval window is invalid")
        current = now or dt.datetime.now(dt.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=dt.timezone.utc)
        current = current.astimezone(dt.timezone.utc)
        if approved_at > current or expires_at <= current:
            raise ProductionModelPolicyError("model policy approval is not currently valid")
        principals: set[str] = set()
        roles: set[str] = set()
        for index, raw_approver in enumerate(raw_approvers):
            approver = _exact_mapping(
                raw_approver,
                _APPROVER_KEYS,
                label=f"model policy approver {index}",
            )
            principal = _text(
                approver["principal"], label=f"approver {index} principal"
            )
            role = _text(approver["role"], label=f"approver {index} role")
            if principal in principals:
                raise ProductionModelPolicyError(
                    "model policy approvers must be distinct"
                )
            principals.add(principal)
            roles.add(role)
            approvers.append((principal, role))
        if not {"model-risk-owner", "security-owner"}.issubset(roles):
            raise ProductionModelPolicyError(
                "model policy requires model-risk-owner and security-owner approval"
            )

    if require_approved and status != "approved":
        raise ProductionModelPolicyError("production model policy is not approved")
    return ProductionModelPolicy(
        path=path,
        sha256=sha256,
        policy_id=policy_id,
        profile_id=profile_id,
        source_version=source_version,
        production_surface_sha256=surface_digest,
        production_surface_digest_mode=surface_digest_mode,
        status=status,
        approved_at=approved_at,
        expires_at=expires_at,
        approvers=tuple(approvers),
        models=models,
        limitations=limitations,
    )


def load_policy_file(
    path: str | Path,
    *,
    production_surface_sha256: str,
    require_approved: bool = False,
    now: dt.datetime | None = None,
    expected_sha256: str | None = None,
) -> ProductionModelPolicy:
    resolved = Path(path).expanduser().absolute()
    from .file_lock import read_verified_bytes

    try:
        raw = read_verified_bytes(
            resolved,
            root=resolved.parent,
            max_bytes=_MAX_POLICY_BYTES,
        )
    except Exception as exc:
        raise ProductionModelPolicyError(
            f"production model policy is unavailable: {resolved}"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        if _HEX_SHA256.fullmatch(expected_sha256) is None:
            raise ProductionModelPolicyError(
                "production model policy expected digest is invalid"
            )
        if digest != expected_sha256:
            raise ProductionModelPolicyError(
                "production model policy digest differs from release authority"
            )
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionModelPolicyError(
            "production model policy is not valid UTF-8 JSON"
        ) from exc
    return validate_policy_document(
        document,
        production_surface_sha256=production_surface_sha256,
        require_approved=require_approved,
        now=now,
        path=resolved,
        sha256=digest,
    )


def _production_required() -> bool:
    from .production_scope import production_surface_required

    return production_surface_required()


def load_runtime_policy() -> ProductionModelPolicy:
    if not _production_required():
        raise ProductionModelPolicyError(
            "runtime model policy is only defined for a production artifact"
        )
    policy_path = os.environ.get(POLICY_ENV, "").strip()
    expected = os.environ.get(POLICY_SHA256_ENV, "").strip()
    if not policy_path or not expected:
        raise ProductionModelPolicyError(
            "production model policy path and release-pinned digest are required"
        )
    from .production_scope import load_manifest

    manifest = load_manifest()
    from .file_lock import read_verified_bytes

    surface_raw = read_verified_bytes(
        manifest.path,
        root=manifest.path.parent,
        max_bytes=_MAX_SURFACE_BYTES,
    )
    if hashlib.sha256(surface_raw).hexdigest() != manifest.sha256:
        raise ProductionModelPolicyError(
            "production surface changed while loading model policy"
        )
    policy = load_policy_file(
        policy_path,
        production_surface_sha256=production_surface_sha256(surface_raw),
        require_approved=True,
        expected_sha256=expected,
    )
    if policy.profile_id != manifest.profile_id:
        raise ProductionModelPolicyError("model policy targets a different profile")
    if policy.source_version != manifest.source_version:
        raise ProductionModelPolicyError("model policy targets a different version")
    return policy


def _runtime_assertion(name: str, expected: str) -> None:
    actual = os.environ.get(name, "").strip()
    if actual != expected:
        raise ProductionModelPolicyError(
            f"{name} is absent or differs from the approved model policy"
        )


def _runtime_cap(model: ProductionModel) -> None:
    from .provider_cost_cap import caps_from_config, period_from_config

    cap = caps_from_config().get(model.provider)
    if cap is None or cap > model.max_spend_usd:
        raise ProductionModelPolicyError(
            f"provider {model.provider} needs a deployment spend cap no greater "
            f"than ${model.max_spend_usd:.2f}"
        )
    if period_from_config() != model.spend_period:
        raise ProductionModelPolicyError(
            f"provider {model.provider} spend period differs from model policy"
        )


def _normalized_runtime_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def require_model_admitted(spec: str) -> str:
    """Return canonical ``provider:model`` or refuse a production dispatch."""
    canonical, _, _ = _canonical_spec(spec)
    if not _production_required():
        return canonical
    policy = load_runtime_policy()
    model = policy.models.get(canonical)
    if model is None:
        raise ProductionModelPolicyError(
            f"model {canonical!r} is outside the approved production policy"
        )
    _runtime_assertion(ARTIFACT_DIGEST_ENV, model.artifact_digest)
    _runtime_assertion(REGION_ENV, model.region)
    _runtime_assertion(RETENTION_ENV, model.retention_mode)
    _runtime_assertion(CREDENTIAL_SOURCE_ENV, model.credential_source)
    _runtime_cap(model)
    return canonical


def require_provider_configuration(
    provider: str,
    *,
    base_url: str | None,
    api_key_present: bool,
) -> None:
    """Bind the admitted provider client's actual endpoint and credential."""
    if not _production_required():
        return
    policy = load_runtime_policy()
    candidates = [model for model in policy.models.values() if model.provider == provider]
    if not candidates:
        raise ProductionModelPolicyError(
            f"provider {provider!r} is outside the approved production policy"
        )
    model = candidates[0]
    if _normalized_runtime_url(base_url) != model.base_url:
        raise ProductionModelPolicyError(
            f"provider {provider!r} endpoint differs from the approved policy"
        )
    if model.credential_source == "mounted_file":
        if os.environ.get("MAVERICK_SECRETS_BACKEND", "").strip().lower() != "file":
            raise ProductionModelPolicyError(
                "mounted-file provider credentials require the file secret backend"
            )
        if not api_key_present:
            raise ProductionModelPolicyError(
                f"provider {provider!r} mounted credential is unavailable"
            )
    elif api_key_present:
        raise ProductionModelPolicyError(
            f"provider {provider!r} received an API key outside its credential policy"
        )


def require_failover_chain(primary: str, chain: Sequence[str]) -> None:
    """Refuse any configured failover destination not pinned by policy."""
    if not _production_required():
        return
    canonical, _, _ = _canonical_spec(primary)
    policy = load_runtime_policy()
    model = policy.models.get(canonical)
    if model is None:
        raise ProductionModelPolicyError(
            f"primary model {canonical!r} is outside the approved policy"
        )
    actual = tuple(_canonical_spec(candidate)[0] for candidate in chain)
    if actual != model.failover:
        raise ProductionModelPolicyError(
            f"failover chain for {canonical!r} differs from the approved policy"
        )
    missing = [candidate for candidate in actual if candidate not in policy.models]
    if missing:
        raise ProductionModelPolicyError(
            f"failover destinations lack full model policy: {missing!r}"
        )


def runtime_policy_violations(spec: str) -> list[str]:
    """Return one bounded startup violation instead of leaking policy internals."""
    try:
        canonical = require_model_admitted(spec)
        _, provider, _ = _canonical_spec(canonical)
        from .llm import LLM

        config = LLM(model=canonical)._provider_client_config(provider)
        require_provider_configuration(
            provider,
            base_url=config.base_url,
            api_key_present=bool(config.api_key),
        )
    except Exception as exc:
        return [f"production model policy admission failed: {exc}"]
    return []


__all__ = [
    "ARTIFACT_DIGEST_ENV",
    "CREDENTIAL_SOURCE_ENV",
    "POLICY_ENV",
    "POLICY_SHA256_ENV",
    "PRODUCTION_SURFACE_DIGEST_MODE",
    "REGION_ENV",
    "RETENTION_ENV",
    "SCHEMA",
    "ProductionModel",
    "ProductionModelPolicy",
    "ProductionModelPolicyError",
    "load_policy_file",
    "load_runtime_policy",
    "production_surface_sha256",
    "require_failover_chain",
    "require_model_admitted",
    "require_provider_configuration",
    "runtime_policy_violations",
    "validate_policy_document",
]
