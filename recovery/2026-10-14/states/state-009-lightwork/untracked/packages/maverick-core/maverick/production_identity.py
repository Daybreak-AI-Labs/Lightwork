"""Canonical, secret-free production identity and RBAC posture.

This module derives one deployment-specific posture from the identity and
static authorization inputs the process actually consumes.  It deliberately
does not include credential values or mutable dashboard/tenant roster files.
The latter require live evidence and change control; hashing them once would
misrepresent a point-in-time snapshot as continuous attestation.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .dashboard_rbac_policy import ROLES, canonical_permission_policy

POSTURE_SCHEMA = "lightwork.production-identity-rbac-posture.v1"
POSTURE_CONTRACT_SCHEMA = "lightwork.production-identity-posture-contract.v1"
PROFILE_ID = "enterprise-single-control-plane-v1"
SUPPORTED_PROVIDERS = frozenset({"oidc", "trusted_proxy_sso"})
PROVIDER_ENV = "MAVERICK_PRODUCTION_IDENTITY_PROVIDER"
POSTURE_DIGEST_ENV = "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_HEADER_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_TRUE = frozenset({"1", "true", "yes", "on", "enable", "enabled", "y", "t"})
_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})
_OIDC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"}
)
_ROLE_SCOPE_FIELDS = frozenset(
    {"allow_tools", "deny_tools", "max_risk", "allow_paths", "allow_hosts"}
)
_RISK_LEVELS = frozenset({"low", "medium", "high"})
_FORBIDDEN_PROXY_HEADERS = frozenset(
    {"authorization", "cookie", "host", "proxy-authorization", "x-forwarded-for"}
)

CREDENTIAL_FIELDS_EXCLUDED = (
    "MAVERICK_OIDC_CLIENT_SECRET",
    "MAVERICK_OIDC_SESSION_SECRET",
    "auth.oidc.client_secret",
    "auth.oidc.session_secret",
)
MUTABLE_AUTHORIZATION_STATE_EXCLUDED = (
    "dashboard-users.json",
    "dashboard-tenant-roles.json",
    "dashboard-user-suites.json",
    "dashboard-tenant-suites.json",
)


class ProductionIdentityError(ValueError):
    """The selected production identity or static RBAC posture is ambiguous."""


@dataclass(frozen=True)
class IdentityPosture:
    provider: str
    document: dict[str, Any]
    canonical_bytes: bytes
    rbac_policy_sha256: str
    sha256: str


def canonical_json_bytes(value: object) -> bytes:
    """Encode strict, deterministic JSON used by both posture digests."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProductionIdentityError("identity posture is not canonical JSON") from exc


def _config(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        try:
            from .config import config_source_errors, load_config

            value = load_config()
            if config_source_errors():
                raise ProductionIdentityError(
                    "production identity configuration source is invalid"
                )
        except ProductionIdentityError:
            raise
        except Exception as exc:
            raise ProductionIdentityError(
                "production identity configuration is unavailable"
            ) from exc
    if not isinstance(value, Mapping):
        raise ProductionIdentityError("production configuration must be a table")
    return value


def _table(parent: Mapping[str, Any], key: str, *, label: str) -> Mapping[str, Any]:
    value = parent.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ProductionIdentityError(f"{label} must be a table")
    return value


def _flag(raw: object, *, label: str, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in {0, 1}:
        return bool(raw)
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
    raise ProductionIdentityError(f"{label} must be an explicit boolean")


def _enabled(env_name: str, configured: object, *, label: str) -> bool:
    raw = os.environ.get(env_name)
    if raw is not None and raw.strip():
        return _flag(raw, label=env_name)
    return _flag(configured, label=label)


def _text(value: object, *, label: str, required: bool = True) -> str:
    if not isinstance(value, str) or value != value.strip() or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        raise ProductionIdentityError(f"{label} must be a trimmed printable string")
    if required and not value:
        raise ProductionIdentityError(f"{label} is required")
    if len(value) > 2048:
        raise ProductionIdentityError(f"{label} is too long")
    return value


def _https_url(value: object, *, label: str, required: bool = True) -> str:
    text = _text(value, label=label, required=required)
    if not text:
        return ""
    parsed = urlparse(text)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProductionIdentityError(
            f"{label} must be an HTTPS URL without credentials, query, or fragment"
        )
    return text


def _raw_algorithms(section: Mapping[str, Any]) -> tuple[str, ...]:
    raw: object = os.environ.get("MAVERICK_OIDC_ALGORITHMS")
    if raw is None:
        raw = section.get("algorithms", ("RS256", "ES256"))
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",")]
    elif isinstance(raw, (list, tuple)):
        values = [item.strip() if isinstance(item, str) else item for item in raw]
    else:
        raise ProductionIdentityError("OIDC algorithms must be a string list")
    if (
        not values
        or any(not isinstance(item, str) or not item for item in values)
        or any(item not in _OIDC_ALGORITHMS for item in values)
    ):
        raise ProductionIdentityError(
            "OIDC algorithms must contain only supported asymmetric algorithms"
        )
    return tuple(sorted(set(values)))


def _oidc_value(
    section: Mapping[str, Any],
    *,
    environment: str,
    key: str,
) -> object:
    """Resolve one non-secret OIDC field from the same config snapshot.

    ``load_oidc_config`` intentionally re-reads global configuration.  A
    production posture may be built from an explicitly supplied, already-read
    deployment snapshot, so re-reading here could mix provider/RBAC values from
    one generation with issuer values from another.  Preserve its non-empty env
    precedence without leaving the supplied snapshot.
    """

    override = os.environ.get(environment)
    return override or section.get(key, "")


def _oidc_mapping(section: Mapping[str, Any]) -> dict[str, Any]:
    issuer = _https_url(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_ISSUER",
            key="issuer",
        ),
        label="OIDC issuer",
    )
    audience = _text(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_AUDIENCE",
            key="audience",
        ),
        label="OIDC audience",
    )
    jwks_uri = _https_url(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_JWKS_URI",
            key="jwks_uri",
        ),
        label="OIDC JWKS URI",
    )
    client_id = _text(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_CLIENT_ID",
            key="client_id",
        ),
        label="OIDC client ID",
    )
    redirect_uri = _https_url(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_REDIRECT_URI",
            key="redirect_uri",
        ),
        label="OIDC redirect URI",
        required=False,
    )
    authorization_endpoint = _https_url(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",
            key="authorization_endpoint",
        ),
        label="OIDC authorization endpoint",
        required=False,
    )
    token_endpoint = _https_url(
        _oidc_value(
            section,
            environment="MAVERICK_OIDC_TOKEN_ENDPOINT",
            key="token_endpoint",
        ),
        label="OIDC token endpoint",
        required=False,
    )
    if bool(authorization_endpoint) != bool(token_endpoint):
        raise ProductionIdentityError(
            "OIDC authorization and token endpoints must be supplied together"
        )
    return {
        "algorithms": list(_raw_algorithms(section)),
        "audience": audience,
        "authorization_endpoint": authorization_endpoint,
        "claim_mapping": {
            "audience_claim": "aud",
            "issued_at_claim": "iat",
            "principal_prefix": "user:",
            "subject_claim": "sub",
            "expiry_claim": "exp",
            "issuer_claim": "iss",
        },
        "client_id": client_id,
        "issuer": issuer,
        "jwks_uri": jwks_uri,
        "redirect_uri": redirect_uri,
        "token_endpoint": token_endpoint,
    }


def _trusted_proxy(value: object) -> str:
    text = _text(value, label="trusted proxy")
    try:
        if "/" in text:
            return str(ipaddress.ip_network(text, strict=True))
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        raise ProductionIdentityError(
            "trusted proxies must be canonical IP addresses or CIDR networks"
        ) from exc


def _proxy_mapping(section: Mapping[str, Any]) -> dict[str, Any]:
    raw_header = os.environ.get("MAVERICK_PROXY_AUTH_HEADER")
    if raw_header is None:
        raw_header = section.get("header") or "X-Forwarded-User"
    header = _text(raw_header, label="trusted-proxy identity header")
    if (
        _HEADER_RE.fullmatch(header) is None
        or header.casefold() in _FORBIDDEN_PROXY_HEADERS
    ):
        raise ProductionIdentityError("trusted-proxy identity header is unsafe")
    trusted = section.get("trusted_proxies")
    if not isinstance(trusted, (list, tuple)) or not trusted:
        raise ProductionIdentityError(
            "trusted-proxy SSO requires an explicit non-empty trusted_proxies list"
        )
    peers = [_trusted_proxy(item) for item in trusted]
    if len(peers) != len(set(peers)):
        raise ProductionIdentityError("trusted-proxy SSO contains duplicate trust entries")
    trust_loopback = _flag(
        section.get("trust_loopback"),
        label="auth.proxy.trust_loopback",
    )
    if trust_loopback:
        raise ProductionIdentityError(
            "production trusted-proxy SSO requires pinned peers, not loopback fallback"
        )
    return {
        "claim_mapping": {
            "header_value": "subject",
            "principal_prefix": "user:",
        },
        "header": header,
        "trusted_proxies": sorted(peers),
        "trust_loopback": False,
    }


def _group_mapping(
    raw: object,
    *,
    label: str,
    roles: bool,
) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ProductionIdentityError(f"{label} must be a table")
    result: dict[str, Any] = {}
    for group, value in raw.items():
        name = _text(group, label=f"{label} group").casefold()
        if name in result:
            raise ProductionIdentityError(f"{label} contains duplicate group names")
        if roles:
            role = _text(value, label=f"{label}[{group!r}]").lower()
            if role not in ROLES:
                raise ProductionIdentityError(f"{label} contains an unknown role")
            result[name] = role
            continue
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, (list, tuple)):
            raise ProductionIdentityError(f"{label} suites must be string lists")
        suites = [_text(item, label=f"{label}[{group!r}]") for item in values]
        result[name] = sorted(set(suites))
    return result


def _bootstrap_admins(dashboard: Mapping[str, Any]) -> list[str]:
    env = os.environ.get("MAVERICK_DASHBOARD_ADMINS")
    if env is not None and env.strip():
        raw: object = [item.strip() for item in env.split(",") if item.strip()]
    else:
        raw = dashboard.get("admins", []) or []
        if isinstance(raw, str):
            raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ProductionIdentityError("dashboard admins must be a string list")
    admins = [_text(item, label="dashboard admin") for item in raw]
    if not admins:
        raise ProductionIdentityError(
            "production requires at least one explicit bootstrap/break-glass admin"
        )
    if any(not item.startswith("user:") or item == "user:" for item in admins):
        raise ProductionIdentityError(
            "dashboard admins must use exact non-empty user: principals"
        )
    return sorted(set(admins))


def _default_suites(dashboard: Mapping[str, Any]) -> list[str] | None:
    if "default_suites" not in dashboard:
        return None
    raw = dashboard.get("default_suites")
    if not isinstance(raw, (list, tuple)):
        raise ProductionIdentityError("dashboard default_suites must be a string list")
    return sorted(
        set(_text(item, label="dashboard default suite") for item in raw)
    )


def _kernel_roles(config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    raw_roles = config.get("roles")
    if raw_roles is None:
        raw_roles = {}
    if not isinstance(raw_roles, Mapping):
        raise ProductionIdentityError("kernel roles must be a table")
    roles: dict[str, Any] = {}
    for raw_name, raw_scope in raw_roles.items():
        name = _text(raw_name, label="kernel role")
        if not isinstance(raw_scope, Mapping):
            raise ProductionIdentityError(f"kernel role {name!r} must be a table")
        unknown = set(raw_scope) - _ROLE_SCOPE_FIELDS
        if unknown:
            raise ProductionIdentityError(
                f"kernel role {name!r} contains unsupported fields: {sorted(unknown)}"
            )
        scope: dict[str, Any] = {}
        for field in ("allow_tools", "deny_tools", "allow_paths", "allow_hosts"):
            raw = raw_scope.get(field, [])
            if not isinstance(raw, (list, tuple)):
                raise ProductionIdentityError(
                    f"kernel role {name!r}.{field} must be a string list"
                )
            scope[field] = sorted(
                set(_text(item, label=f"kernel role {name!r}.{field}") for item in raw)
            )
        risk = raw_scope.get("max_risk")
        if risk is not None:
            risk = _text(risk, label=f"kernel role {name!r}.max_risk").lower()
            if risk not in _RISK_LEVELS:
                raise ProductionIdentityError(
                    f"kernel role {name!r}.max_risk is unsupported"
                )
        scope["max_risk"] = risk
        roles[name] = scope

    raw_assignments = config.get("role_assignments")
    if raw_assignments is None:
        raw_assignments = {}
    if not isinstance(raw_assignments, Mapping):
        raise ProductionIdentityError("kernel role_assignments must be a table")
    assignments: dict[str, str] = {}
    for raw_principal, raw_role in raw_assignments.items():
        principal = _text(raw_principal, label="kernel role-assignment principal")
        role = _text(raw_role, label=f"kernel role assignment for {principal!r}")
        if role not in roles:
            raise ProductionIdentityError(
                f"kernel role assignment references unknown role {role!r}"
            )
        assignments[principal] = role
    return roles, assignments


def _rbac_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    dashboard = _table(config, "dashboard", label="dashboard configuration")
    if "default_role" not in dashboard:
        raise ProductionIdentityError(
            "production requires explicit dashboard.default_role = viewer"
        )
    default_role = dashboard.get("default_role")
    default_role = _text(default_role, label="dashboard default role").lower()
    if default_role != "viewer":
        raise ProductionIdentityError(
            "production dashboard.default_role must be viewer"
        )
    roles, assignments = _kernel_roles(config)
    return {
        "dashboard": {
            "bootstrap_admins": _bootstrap_admins(dashboard),
            "default_role": default_role,
            "default_suites": _default_suites(dashboard),
            "group_roles": _group_mapping(
                dashboard.get("group_roles"),
                label="dashboard.group_roles",
                roles=True,
            ),
            "group_suites": _group_mapping(
                dashboard.get("group_suites"),
                label="dashboard.group_suites",
                roles=False,
            ),
            "role_permissions": canonical_permission_policy(),
        },
        "kernel": {
            "role_assignments": assignments,
            "roles": roles,
        },
    }


def _unsupported_identity(config: Mapping[str, Any]) -> None:
    if os.environ.get("MAVERICK_DASHBOARD_TOKEN", "").strip():
        raise ProductionIdentityError(
            "the shared dashboard operator token is a static dashboard credential "
            "unsupported in production"
        )
    auth = _table(config, "auth", label="auth configuration")
    saml = auth.get("saml")
    if saml is not None and (not isinstance(saml, Mapping) or bool(saml)):
        raise ProductionIdentityError("SAML identity is unsupported in production")
    saml_env = os.environ.get("MAVERICK_SAML_ENABLED")
    if saml_env is not None and _flag(saml_env, label="MAVERICK_SAML_ENABLED"):
        raise ProductionIdentityError("SAML identity is unsupported in production")
    dashboard = _table(config, "dashboard", label="dashboard configuration")
    invite_env = os.environ.get("MAVERICK_DASHBOARD_INVITES")
    invites = (
        _flag(invite_env, label="MAVERICK_DASHBOARD_INVITES")
        if invite_env is not None and invite_env.strip()
        else _flag(dashboard.get("invites"), label="dashboard.invites")
    )
    if invites:
        raise ProductionIdentityError("local invite identity is unsupported in production")


def runtime_identity_posture(
    *,
    config: Mapping[str, Any] | None = None,
) -> IdentityPosture:
    """Derive the canonical secret-free posture from effective runtime inputs."""

    selected_config = _config(config)
    _unsupported_identity(selected_config)
    auth = _table(selected_config, "auth", label="auth configuration")
    oidc_section = _table(auth, "oidc", label="auth.oidc")
    proxy_section = _table(auth, "proxy", label="auth.proxy")
    oidc_enabled = _enabled(
        "MAVERICK_OIDC_ENABLED",
        oidc_section.get("enabled"),
        label="auth.oidc.enabled",
    )
    proxy_enabled = _enabled(
        "MAVERICK_PROXY_AUTH",
        proxy_section.get("enabled"),
        label="auth.proxy.enabled",
    )
    active = [
        provider
        for provider, enabled in (
            ("oidc", oidc_enabled),
            ("trusted_proxy_sso", proxy_enabled),
        )
        if enabled
    ]
    if len(active) != 1:
        raise ProductionIdentityError(
            "production requires exactly one configured provider: OIDC or trusted-proxy SSO"
        )
    provider = active[0]
    mapping = (
        _oidc_mapping(oidc_section)
        if provider == "oidc"
        else _proxy_mapping(proxy_section)
    )
    rbac_policy = _rbac_policy(selected_config)
    rbac_bytes = canonical_json_bytes(rbac_policy)
    rbac_digest = hashlib.sha256(rbac_bytes).hexdigest()
    document = {
        "credential_fields_excluded": list(CREDENTIAL_FIELDS_EXCLUDED),
        "identity_mapping": mapping,
        "mutable_authorization_state": {
            "included_in_digest": False,
            "paths": list(MUTABLE_AUTHORIZATION_STATE_EXCLUDED),
            "reason": "requires live evidence and authorized-change requalification",
        },
        "profile_id": PROFILE_ID,
        "rbac_policy": rbac_policy,
        "rbac_policy_sha256": rbac_digest,
        "schema": POSTURE_SCHEMA,
        "selected_provider": provider,
    }
    encoded = canonical_json_bytes(document)
    return IdentityPosture(
        provider=provider,
        document=document,
        canonical_bytes=encoded,
        rbac_policy_sha256=rbac_digest,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def trusted_runtime_binding() -> tuple[str, str]:
    provider = os.environ.get(PROVIDER_ENV, "").strip()
    digest = os.environ.get(POSTURE_DIGEST_ENV, "").strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ProductionIdentityError(
            f"{PROVIDER_ENV} must select exactly oidc or trusted_proxy_sso"
        )
    if _DIGEST_RE.fullmatch(digest) is None or digest == "0" * 64:
        raise ProductionIdentityError(
            f"{POSTURE_DIGEST_ENV} must be one non-zero lowercase SHA-256"
        )
    return provider, digest


def validate_runtime_identity_posture(
    *,
    config: Mapping[str, Any] | None = None,
) -> IdentityPosture:
    """Bind effective identity/RBAC inputs to out-of-band runtime trust pins."""

    posture = runtime_identity_posture(config=config)
    trusted_provider, trusted_digest = trusted_runtime_binding()
    if posture.provider != trusted_provider:
        raise ProductionIdentityError(
            "configured identity provider differs from the trusted runtime selection"
        )
    if posture.sha256 != trusted_digest:
        raise ProductionIdentityError(
            "identity/RBAC posture differs from the trusted runtime digest"
        )
    return posture


__all__ = [
    "CREDENTIAL_FIELDS_EXCLUDED",
    "IdentityPosture",
    "MUTABLE_AUTHORIZATION_STATE_EXCLUDED",
    "POSTURE_CONTRACT_SCHEMA",
    "POSTURE_DIGEST_ENV",
    "POSTURE_SCHEMA",
    "PROFILE_ID",
    "PROVIDER_ENV",
    "ProductionIdentityError",
    "SUPPORTED_PROVIDERS",
    "canonical_json_bytes",
    "runtime_identity_posture",
    "trusted_runtime_binding",
    "validate_runtime_identity_posture",
]
