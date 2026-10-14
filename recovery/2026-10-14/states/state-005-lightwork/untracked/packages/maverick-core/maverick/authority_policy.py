"""One mode decision for every authority-bearing ingress.

Community deployments retain the historical compatibility behavior.  Strict
mode removes shared/synthetic principals and unsigned internal traffic from
authority paths.  Enterprise mode is always strict, and an unreadable policy
source is treated as strict because it may contain an enterprise requirement
that the process cannot safely inspect.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum


class AuthorityMode(str, Enum):
    """Authority-plane operating modes."""

    COMPAT = "compat"
    STRICT = "strict"


_UNSET = object()
_SYNTHETIC_PRINCIPALS = frozenset({
    "anon",
    "anonymous",
    "a2a",
    "grpc",
    "mcp",
    "operator",
    "shared",
})


def authority_mode(
    *,
    config: object = _UNSET,
    source_errors: object = _UNSET,
    enterprise: object = _UNSET,
) -> AuthorityMode:
    """Resolve ``[authority] mode`` with mandatory enterprise floors.

    The injectable arguments make the decision pure in tests and embedders.
    Omitted values are loaded from Maverick's live policy sources.  Any failure
    to read those sources returns :attr:`AuthorityMode.STRICT`; uncertainty
    must never silently restore a shared or unsigned authority path.
    """
    if config is _UNSET or source_errors is _UNSET:
        try:
            from .config import config_source_errors, load_global_config

            if config is _UNSET:
                # Authority mode is deployment-global. A tenant overlay must
                # never weaken the identity floor for shared ingress paths.
                config = load_global_config()
            if source_errors is _UNSET:
                source_errors = config_source_errors(include_tenant=False)
        except Exception:
            return AuthorityMode.STRICT
    if source_errors:
        return AuthorityMode.STRICT
    if not isinstance(config, Mapping):
        return AuthorityMode.STRICT

    if enterprise is _UNSET:
        try:
            from .enterprise import deployment_enterprise_enabled

            # Use the exact global snapshot already trusted above.  Re-reading
            # enterprise policy through load_config() would admit tenant overlays.
            enterprise = deployment_enterprise_enabled(
                config=config,
                source_errors=source_errors,
            )
        except Exception:
            return AuthorityMode.STRICT
    if enterprise is True:
        return AuthorityMode.STRICT
    if enterprise not in (False, None):
        return AuthorityMode.STRICT

    section = config.get("authority")
    if section is None:
        return AuthorityMode.COMPAT
    if not isinstance(section, Mapping):
        return AuthorityMode.STRICT
    mode = section.get("mode", AuthorityMode.COMPAT.value)
    if not isinstance(mode, str):
        return AuthorityMode.STRICT
    normalized = mode.strip().lower()
    if normalized == AuthorityMode.COMPAT.value:
        return AuthorityMode.COMPAT
    if normalized == AuthorityMode.STRICT.value:
        return AuthorityMode.STRICT
    return AuthorityMode.STRICT


def strict_authority() -> bool:
    """Return whether authority-bearing paths must use strict identity."""
    return authority_mode() is AuthorityMode.STRICT


def is_individual_principal(principal: object) -> bool:
    """True for an attributable principal, false for shared/synthetic ones."""
    if not isinstance(principal, str):
        return False
    normalized = principal.strip().lower()
    if not normalized or normalized in _SYNTHETIC_PRINCIPALS:
        return False
    return not normalized.startswith(("bearer:", "shared:", "synthetic:"))


__all__ = [
    "AuthorityMode",
    "authority_mode",
    "is_individual_principal",
    "strict_authority",
]
