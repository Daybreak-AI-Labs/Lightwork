"""Config validator for ``~/.maverick/config.toml`` (Lightwork 2.0 RFC item).

Walks an already-loaded config dict (see :func:`maverick.config.load_config`)
and reports likely mistakes: a mistyped section name, an unknown key inside a
section whose key set is fixed, and a small, safe set of obvious type errors
(``budget.max_dollars`` must be a number; ``*.enabled`` must be a bool).

Design constraints:
- Pure stdlib (``difflib`` for "did you mean" suggestions), deterministic.
- Conservative: only flag things we're confident about. Dynamic sections —
  ``[providers.<name>]``, ``[channels.<name>]``, ``[models]``,
  ``[mcp_servers.<name>]``, ``[roles.<role>]`` and similar — accept any
  subkeys, so we never flag their keys.
- Never raises on a weird dict. A non-dict section value, a non-string key,
  ``None`` — all tolerated; we simply skip what we can't reason about.

This is advisory only. The kernel itself fails soft on a bad config (see
``maverick.config.load_config``); this lets the wizard / a ``maverick config
lint`` command surface problems up front.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass
from typing import Any

# Known top-level sections -> allowed keys, or ``None`` to allow ANY key.
#
# ``None`` is used for two kinds of section:
#   * genuinely dynamic tables whose subkeys are user-chosen names
#     (``models`` role->spec, ``providers.<name>``, ``channels.<name>``,
#     ``mcp_servers.<name>``, ``roles.<role>``), and
#   * sections whose key set is still evolving / spread across modules, where
#     enumerating keys would produce false-positive warnings on valid configs.
#
# Only sections with a small, stable, documented key set get an explicit set;
# those are the ones where an unknown key is a useful signal.
KNOWN_SCHEMA: dict[str, set[str] | None] = {
    # --- fixed-key sections (unknown keys are flagged) ---
    "deployment": {"type", "allow_multiple_control_planes"},
    "budget": {
        "max_dollars",
        "max_wall_seconds",
        "max_tool_calls",
        "max_input_tokens",
        "max_output_tokens",
        # Default-on per-task-class self-tuning of max_dollars (self_tuning_budget.py
        # reads [budget] self_tuning; self_healing.py recommends it). Without it
        # here, a client enabling the documented knob got a false "unknown key".
        "self_tuning",
    },
    "safety": {
        "profile",
        "block_threshold",
        "scan_input",
        "scan_tool_calls",
        "scan_output",
        "constitution",
        "compartments",
        "compartment_unseal",
    },
    "sandbox": {
        "backend", "workdir", "timeout", "image", "language",
        "require_container", "allow_network", "allow_root", "pids_limit",
        "memory", "memory_mb", "cpus", "runtime", "options",
        "reuse_container", "cross_run_pool", "project_dir", "namespace",
        "context", "extra_kubectl_args", "run_as_user", "provider",
        "api_key", "network", "warm", "host", "ssh_args",
        "host_key_checking",
    },
    "features": {"skills", "world_model", "streaming"},
    "capabilities": {
        "computer_use",
        "browser",
        "web_search",
        "mobile_tools",
        "code_exec",
        # Governance knobs the runtime reads (capability.py / agent.py) that
        # were missing here -- so a client configuring the flagship
        # capability-enforcement feature, or the deferred-tools knob, got a
        # false "unknown key" warning (client-journey finding).
        "enforce",
        "per_call_tokens",
        "deferred_tools",
    },
    # Tamper-evident audit log. The runtime reads [audit] sign (audit/writer.py)
    # and migrate.py already lists it; config-lint flagged the whole section as
    # unknown ("did you mean auth?"), telling a regulated client their flagship
    # signed-audit config looked like a typo (client-journey finding).
    "audit": {"sign", "worm"},
    "durable": {"enabled", "keep_last"},
    "persona": {"name", "style", "addendum"},
    # The dashboard reads more than the auth token: theme/density/allow_extension
    # (app.py) and a [dashboard.themes] subtable (themes.py). Listing only
    # "token" made config-lint warn "unknown key" on documented operator
    # settings like `[dashboard] theme = "dark"` (user-testing finding).
    # public_url: the externally-reachable base URL used to build signed flow
    # approval links (automation_queue._public_base_url); without it here a client
    # configuring actionable channel approvals got a false "unknown key".
    # default_suites: deny-by-default department scoping for authenticated
    # dashboard users with no explicit grant (maverick.suite_grants).
    # group_roles / group_suites: SCIM-group -> role / department mapping
    # tables (maverick_dashboard.scim_groups), so access flows from IdP team
    # membership.
    "dashboard": {"token", "theme", "density", "allow_extension", "themes", "public_url",
                  "default_suites", "group_roles", "group_suites"},
    "analytics": {"mcp_client_language"},
    # Work discovery is a high-trust client-controlled sensor.  Keep this
    # section fixed-key so misspelled retention/privacy knobs do not silently
    # fall back to defaults.
    "ekko": {
        "enable",
        "retention_days",
        "enrollment_days",
        "min_occurrences",
        "min_distinct_days",
        "poll_interval_seconds",
        "capture_level",
        "allowed_apps",
        "blocked_apps",
        "provider_egress",
    },
    # Specialist-model training and promotion is a high-authority mutation
    # boundary.  Keep its schema closed so misspelled receipt, boundary, or
    # sample-floor controls cannot silently fall back to defaults.
    "model_improvement": {
        "enable",
        "allow_hosted",
        "allow_cross_tenant",
        "require_signed_receipt",
        "minimum_train_families",
        "minimum_holdout_families",
    },
    # Definition import reaches third-party APIs and can materialize recurring
    # work, so both opt-ins are closed-schema booleans rather than permissive
    # string truthiness.
    "automation_import": {"enable", "create_schedules"},
    # Evidence release is a high-trust, fail-closed boundary.  V1 deliberately
    # exposes only the master opt-in; misspelled enforcement keys must not
    # silently create an open-ended policy surface.
    "evidence_gateway": {"enable"},
    # AI-governance admission capabilities are explicit, default-off
    # authorities. Keep these schemas closed so a misspelled safety limit does
    # not silently fall back to a more permissive value.
    "ai_inventory": {
        "enable",
        "connectors",
        "page_cap",
        "record_cap",
        "retry_attempts",
        "timeout_seconds",
        "missing_grace_runs",
        "poll_seconds",
    },
    "ai_vendor_risk": {"enable", "portal_enable"},
    "policy_packs": {"enable"},
    "portfolio_risk": {"enable"},
    "drift_monitoring": {
        "enable",
        "bins",
        "min_samples",
        "psi_threshold",
        "kl_threshold",
        "embedding_cosine_threshold",
        "performance_decay_threshold",
        "evidence_validity_seconds",
    },
    "agent_topology": {"enable"},
    "ai_regulatory_feed": {"enable"},
    "red_team_assurance": {
        "enable",
        "commitment_secret_ref",
        "max_cases",
        "max_response_bytes",
        "campaign_ttl_seconds",
        "evidence_validity_seconds",
        "scheduler_poll_seconds",
    },
    "eu_ai_incidents": {"enable", "authorities"},
    "grc_outbound_sync": {
        "enable",
        "targets",
        "max_attempts_per_revision",
        "servicenow_irm",
        "archer",
    },
    "ai_literacy": {"enable"},
    "ai_business_intake": {"enable"},
    "ai_cost_roi": {
        "enable",
        "collector_poll_seconds",
        "collector_lookback_days",
        "collector_limits",
        "aws",
        "azure",
        "gcp",
        "openai",
    },
    "ai_dynamic_risk": {"enable"},
    "ai_policy_management": {"enable"},
    "ai_runtime_guardrails": {"enable"},
    "ai_explainability": {"enable"},
    "ai_conformity_workflow": {"enable"},
    "ai_insurability": {"enable"},
    "ai_regulated_verticals": {"enable"},
    "ai_bank_mrm": {"enable"},
    "ai_benchmark_exchange": {"enable", "trust_roots", "coordinator"},
    "ai_discovery_sensors": {"enable"},
    "ai_media_provenance": {"enable", "verification_adapter"},
    "ai_identity_governance_connectors": {
        "enable",
        "entra",
        "okta",
        "purview",
        "scheduler",
    },
    # --- dynamic / open-ended sections (any subkey accepted) ---
    "providers": None,
    "models": None,
    "channels": None,
    "mcp_servers": None,
    "roles": None,
    "routing": None,
    "planning": None,
    "context": None,
    "reflexion": None,
    "effort": None,
    "cache": None,
    "quotas": None,
    "tenancy": None,
    "retention": None,
    "world_model": None,
    "memory": None,
    "voice": None,
    "webhooks": None,
    "a2a": None,
    "auth": None,
    "knowledge": None,
    "skills": None,
    "self_learning": None,
    "autonomy": None,
    "calibration": None,
    "credit": None,
    "adaptive_compute": None,
    "search": None,
    "skill_synthesis": None,
    "experience": None,
    "compliance": None,
    "security": None,
    "plugins": None,
    "tools": None,
}

# Keep this registry in lockstep with migrate.py's KNOWN_SECTIONS (curated
# from the real load_config() call sites) so config-lint never false-flags a
# section the runtime actually reads as a typo. The two had drifted by ~59
# sections -- a client configuring documented features like [provider_failover],
# [enterprise], [egress], [governance], [encryption] got told they were
# typos (client-journey finding). Sections with an explicit key schema above
# keep it (setdefault won't overwrite); the rest accept any subkey (None),
# exactly as migrate treats them.
from .migrate import KNOWN_SECTIONS as _RUNTIME_SECTIONS  # noqa: E402

for _section in _RUNTIME_SECTIONS:
    KNOWN_SCHEMA.setdefault(_section, None)


@dataclass
class Finding:
    section: str
    key: str | None
    severity: str  # "error" | "warning"
    message: str


# Keys that, when present, must be a number (int/float, but not bool — in
# Python ``bool`` is an ``int`` subclass, so we exclude it explicitly).
# Section -> key.
_NUMERIC_KEYS: dict[str, set[str]] = {
    "budget": {
        "max_dollars",
        "max_wall_seconds",
        "max_tool_calls",
        "max_input_tokens",
        "max_output_tokens",
    },
    "sandbox": {"timeout", "pids_limit", "memory_mb", "cpus", "run_as_user"},
    "durable": {"keep_last"},
    "ekko": {
        "retention_days",
        "enrollment_days",
        "min_occurrences",
        "min_distinct_days",
        "poll_interval_seconds",
    },
}

# Integer-valued controls. These are kept separate from ``_NUMERIC_KEYS``:
# accepting ``20.5`` as a number would make config lint report success while
# the runtime safely falls back to 20, obscuring an operator mistake.
_INTEGER_KEYS: dict[str, set[str]] = {
    "model_improvement": {
        "minimum_train_families",
        "minimum_holdout_families",
    },
}

# Keys that, when present, must be a bool. Section -> key. Plus the universal
# ``enabled``/``enable`` toggle, handled separately for every known section.
_BOOL_KEYS: dict[str, set[str]] = {
    "safety": {"scan_input", "scan_tool_calls", "scan_output", "compartments"},
    "features": {"skills", "world_model", "streaming"},
    "capabilities": {
        "computer_use",
        "browser",
        "web_search",
        "mobile_tools",
        "code_exec",
        "enforce",
        "per_call_tokens",
        "deferred_tools",
    },
    "audit": {"sign"},
    "sandbox": {"require_container", "allow_network", "allow_root",
                "reuse_container", "cross_run_pool", "warm"},
    "analytics": {"mcp_client_language"},
    "self_learning": {
        "preflight", "create_tools", "provision_packs",
        "allow_mcp_acquisition", "allow_provider_egress", "distill_local",
    },
    "ekko": {"provider_egress"},
    "model_improvement": {
        "enable",
        "allow_hosted",
        "allow_cross_tenant",
        "require_signed_receipt",
    },
    "automation_import": {"enable", "create_schedules"},
    "ai_vendor_risk": {"portal_enable"},
    "models": {"cascade"},
    "routing": {"cost_aware"},
}

_UNIVERSAL_BOOL_KEYS = ("enabled", "enable")
_MODEL_IMPROVEMENT_FAMILY_MIN = 20
_MODEL_IMPROVEMENT_FAMILY_MAX = 1_000_000

_IDENTITY_SCHEDULE_KEYS = {
    "enable",
    "interval_seconds",
    "retry_base_seconds",
    "retry_max_seconds",
    "max_attempts_per_slot",
    "lease_seconds",
    "history_limit",
    "max_snapshot_items",
    "max_delta_digests",
}
_NESTED_GOVERNANCE_SCHEMA: dict[tuple[str, ...], set[str]] = {
    ("ai_benchmark_exchange", "trust_roots"): {"schema", "version", "keys"},
    ("ai_benchmark_exchange", "coordinator"): {
        "enable",
        "origin",
        "timeout_seconds",
        "max_request_bytes",
        "max_response_bytes",
    },
    ("ai_identity_governance_connectors", "scheduler"): {
        "enable",
        "poll_seconds",
        "providers",
    },
    ("ai_identity_governance_connectors", "scheduler", "providers"): {
        "entra",
        "okta",
        "purview",
    },
}
for _provider in ("entra", "okta", "purview"):
    _NESTED_GOVERNANCE_SCHEMA[
        ("ai_identity_governance_connectors", "scheduler", "providers", _provider)
    ] = _IDENTITY_SCHEDULE_KEYS

_IDENTITY_SCHEDULE_INTEGER_BOUNDS = {
    "interval_seconds": (60, 86_400),
    "retry_base_seconds": (1, 3_600),
    "retry_max_seconds": (1, 86_400),
    "max_attempts_per_slot": (1, 10),
    "lease_seconds": (60, 86_400),
    "history_limit": (1, 32),
    "max_snapshot_items": (1, 10_000),
    "max_delta_digests": (0, 512),
}
_MISSING = object()


# Key names that carry secrets. A literal (non-``${ENV}``) string under one of
# these is a plaintext secret in config.toml -- flagged so operators route it
# through an env var / secrets manager instead. Conservative to avoid noise.
# Bare ``token`` is deliberately NOT here: it is a documented operator setting
# (e.g. ``[dashboard] token``) and too broad to flag without false positives.
# Secret-bearing ``*_token`` keys are still caught by the suffix list below.
_SECRET_KEY_NAMES = frozenset({
    "api_key", "apikey", "secret", "client_secret", "password", "passwd",
    "private_key",
})
_SECRET_KEY_SUFFIXES = ("_api_key", "_secret", "_password", "_token")


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return k in _SECRET_KEY_NAMES or k.endswith(_SECRET_KEY_SUFFIXES)


def _looks_like_env_ref(v: str) -> bool:
    """True for an env-var reference or an empty placeholder -- i.e. NOT an inline
    secret. ``${VAR}`` / ``$VAR`` interpolate from the environment; ``""`` is an
    unset placeholder. Anything else under a secret-bearing key is literal."""
    s = v.strip()
    return s == "" or s.startswith("$")


def _lint_inline_secrets(cfg: dict, _path: tuple[str, ...] = ()) -> list[Finding]:
    """Walk the whole config (nested tables included) and warn on a literal
    string under a secret-bearing key -- a plaintext secret in config.toml."""
    out: list[Finding] = []
    for key, val in cfg.items():
        if not isinstance(key, str):
            continue
        if isinstance(val, dict):
            out.extend(_lint_inline_secrets(val, _path + (key,)))
        elif (
            _is_secret_key(key)
            and isinstance(val, str)
            and not _looks_like_env_ref(val)
        ):
            dotted = ".".join(_path + (key,))
            out.append(Finding(
                section=".".join(_path) or "(root)",
                key=key,
                severity="warning",
                message=(
                    f"{dotted} looks like an inline secret; reference an env var "
                    f'instead (e.g. {key} = "${{MY_SECRET}}") so the secret is not '
                    "stored in plaintext config."
                ),
            ))
    return out


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_valid_numeric_cap(v: int | float) -> bool:
    """Return whether a numeric cap is finite and non-negative.

    ``math.isfinite`` coerces arbitrary-size Python ints to float, which can
    raise ``OverflowError`` for huge TOML integers. Integers are always finite,
    so handle them before checking floats.
    """
    if isinstance(v, int):
        return v >= 0
    return math.isfinite(v) and v >= 0


def _number_between(value: object, lower: float, upper: float) -> bool:
    """Bound a number without coercing arbitrarily large integers to float."""
    if not _is_number(value):
        return False
    if isinstance(value, int):
        return lower <= value <= upper
    return math.isfinite(value) and lower <= value <= upper


def _suggest(name: str, candidates: list[str]) -> str:
    """Return ' (did you mean "X"?)' for the closest candidate, else ''."""
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
    if matches:
        return f' (did you mean "{matches[0]}"?)'
    return ""


def _at_path(cfg: dict, path: tuple[str, ...]) -> object:
    current: object = cfg
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _nested_error(
    section: str,
    key: str,
    message: str,
    *,
    severity: str = "error",
) -> Finding:
    return Finding(section=section, key=key, severity=severity, message=message)


def _lint_identity_schedule_table(
    path: tuple[str, ...], value: dict
) -> list[Finding]:
    findings: list[Finding] = []
    section = ".".join(path)
    enabled = value.get("enable", _MISSING)
    if enabled is not _MISSING and not isinstance(enabled, bool):
        findings.append(
            _nested_error(
                section,
                "enable",
                f"{section}.enable must be true or false, got "
                f"{type(enabled).__name__}",
            )
        )
    for key, bounds in _IDENTITY_SCHEDULE_INTEGER_BOUNDS.items():
        item = value.get(key, _MISSING)
        if item is _MISSING:
            continue
        lower, upper = bounds
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or not lower <= item <= upper
        ):
            findings.append(
                _nested_error(
                    section,
                    key,
                    f"{section}.{key} must be an integer between "
                    f"{lower} and {upper}, got {item!r}",
                )
            )
    retry_base = value.get("retry_base_seconds")
    retry_max = value.get("retry_max_seconds")
    if (
        isinstance(retry_base, int)
        and not isinstance(retry_base, bool)
        and isinstance(retry_max, int)
        and not isinstance(retry_max, bool)
        and retry_max < retry_base
    ):
        findings.append(
            _nested_error(
                section,
                "retry_max_seconds",
                f"{section}.retry_max_seconds must be greater than or equal "
                "to retry_base_seconds",
            )
        )
    return findings


def _lint_trust_root_keys(value: dict) -> list[Finding]:
    findings: list[Finding] = []
    section = "ai_benchmark_exchange.trust_roots"
    version = value.get("version", _MISSING)
    if version is not _MISSING and (
        isinstance(version, bool) or not isinstance(version, int) or version < 1
    ):
        findings.append(
            _nested_error(
                section,
                "version",
                f"{section}.version must be a positive integer",
            )
        )
    schema = value.get("schema", _MISSING)
    if schema is not _MISSING and not isinstance(schema, str):
        findings.append(
            _nested_error(
                section,
                "schema",
                f"{section}.schema must be a string",
            )
        )
    keys = value.get("keys", _MISSING)
    if keys is _MISSING:
        return findings
    if not isinstance(keys, list):
        findings.append(
            _nested_error(
                section,
                "keys",
                f"{section}.keys must be an array of tables",
            )
        )
        return findings
    allowed = {"key_id", "public_key_hex", "introduced_in", "revoked_in"}
    required = {"key_id", "public_key_hex", "introduced_in"}
    for index, item in enumerate(keys):
        item_section = f"{section}.keys[{index}]"
        if not isinstance(item, dict):
            findings.append(
                _nested_error(
                    section,
                    "keys",
                    f"{item_section} must be a table",
                )
            )
            continue
        for key in item:
            if isinstance(key, str) and key not in allowed:
                findings.append(
                    _nested_error(
                        item_section,
                        key,
                        f'unknown key "{key}" in [{item_section}]'
                        + _suggest(key, sorted(allowed)),
                        severity="warning",
                    )
                )
        missing = sorted(required - set(item))
        for key in missing:
            findings.append(
                _nested_error(
                    item_section,
                    key,
                    f"{item_section}.{key} is required",
                )
            )
        for key in ("key_id", "public_key_hex"):
            item_value = item.get(key, _MISSING)
            if item_value is not _MISSING and not isinstance(item_value, str):
                findings.append(
                    _nested_error(
                        item_section,
                        key,
                        f"{item_section}.{key} must be a string",
                    )
                )
        for key in ("introduced_in", "revoked_in"):
            item_value = item.get(key, _MISSING)
            if item_value is not _MISSING and (
                isinstance(item_value, bool)
                or not isinstance(item_value, int)
                or item_value < 1
            ):
                findings.append(
                    _nested_error(
                        item_section,
                        key,
                        f"{item_section}.{key} must be a positive integer",
                    )
                )
    return findings


def _lint_governance_nested_tables(cfg: dict) -> list[Finding]:
    """Validate the few security-sensitive nested governance tables we own."""

    findings: list[Finding] = []
    for path, allowed in _NESTED_GOVERNANCE_SCHEMA.items():
        value = _at_path(cfg, path)
        if value is _MISSING:
            continue
        section = ".".join(path)
        if not isinstance(value, dict):
            findings.append(
                _nested_error(
                    ".".join(path[:-1]),
                    path[-1],
                    f"{section} must be a table, got {type(value).__name__}",
                )
            )
            continue
        for key in value:
            if isinstance(key, str) and key not in allowed:
                findings.append(
                    _nested_error(
                        section,
                        key,
                        f'unknown key "{key}" in [{section}]'
                        + _suggest(key, sorted(allowed)),
                        severity="warning",
                    )
                )
        if len(path) == 4 and path[:3] == (
            "ai_identity_governance_connectors",
            "scheduler",
            "providers",
        ):
            findings.extend(_lint_identity_schedule_table(path, value))

    scheduler = _at_path(cfg, ("ai_identity_governance_connectors", "scheduler"))
    if isinstance(scheduler, dict):
        enabled = scheduler.get("enable", _MISSING)
        if enabled is not _MISSING and not isinstance(enabled, bool):
            findings.append(
                _nested_error(
                    "ai_identity_governance_connectors.scheduler",
                    "enable",
                    "ai_identity_governance_connectors.scheduler.enable must be "
                    f"true or false, got {type(enabled).__name__}",
                )
            )
        poll = scheduler.get("poll_seconds", _MISSING)
        if poll is not _MISSING and not _number_between(poll, 5, 3_600):
            findings.append(
                _nested_error(
                    "ai_identity_governance_connectors.scheduler",
                    "poll_seconds",
                    "ai_identity_governance_connectors.scheduler.poll_seconds "
                    f"must be between 5 and 3600, got {poll!r}",
                )
            )

    trust_roots = _at_path(cfg, ("ai_benchmark_exchange", "trust_roots"))
    if isinstance(trust_roots, dict):
        findings.extend(_lint_trust_root_keys(trust_roots))

    coordinator = _at_path(cfg, ("ai_benchmark_exchange", "coordinator"))
    if isinstance(coordinator, dict):
        section = "ai_benchmark_exchange.coordinator"
        enabled = coordinator.get("enable", _MISSING)
        if enabled is not _MISSING and not isinstance(enabled, bool):
            findings.append(
                _nested_error(
                    section,
                    "enable",
                    f"{section}.enable must be true or false, got "
                    f"{type(enabled).__name__}",
                )
            )
        origin = coordinator.get("origin", _MISSING)
        if origin is not _MISSING and not isinstance(origin, str):
            findings.append(
                _nested_error(
                    section,
                    "origin",
                    f"{section}.origin must be a string",
                )
            )
        if enabled is True and (not isinstance(origin, str) or not origin.strip()):
            findings.append(
                _nested_error(
                    section,
                    "origin",
                    f"enabled {section} requires an explicit origin",
                )
            )
        timeout = coordinator.get("timeout_seconds", _MISSING)
        if timeout is not _MISSING and not _number_between(timeout, 1, 60):
            findings.append(
                _nested_error(
                    section,
                    "timeout_seconds",
                    f"{section}.timeout_seconds must be between 1 and 60",
                )
            )
        for key, upper in (
            ("max_request_bytes", 256 * 1_024),
            ("max_response_bytes", 1_024 * 1_024),
        ):
            item = coordinator.get(key, _MISSING)
            if item is not _MISSING and (
                isinstance(item, bool)
                or not isinstance(item, int)
                or not 1_024 <= item <= upper
            ):
                findings.append(
                    _nested_error(
                        section,
                        key,
                        f"{section}.{key} must be an integer between 1024 "
                        f"and {upper}",
                    )
                )
    return findings


def lint_config(cfg: dict) -> list[Finding]:
    """Validate a loaded config dict and return findings (possibly empty).

    Never raises: anything we can't interpret is skipped rather than flagged.
    """
    findings: list[Finding] = []
    if not isinstance(cfg, dict):
        return findings

    known_sections = sorted(KNOWN_SCHEMA)

    for section, value in cfg.items():
        if not isinstance(section, str):
            continue

        if section not in KNOWN_SCHEMA:
            findings.append(
                Finding(
                    section=section,
                    key=None,
                    severity="warning",
                    message=(
                        f'unknown config section "{section}"'
                        + _suggest(section, known_sections)
                    ),
                )
            )
            continue

        if not isinstance(value, dict):
            # A section is expected to be a table; a scalar/list here is almost
            # certainly a mistake, but we only own the cases we're sure about.
            continue

        allowed = KNOWN_SCHEMA[section]
        numeric_keys = _NUMERIC_KEYS.get(section, set())
        integer_keys = _INTEGER_KEYS.get(section, set())
        bool_keys = _BOOL_KEYS.get(section, set())

        for key, kval in value.items():
            if not isinstance(key, str):
                continue

            # Unknown key in a fixed-key section.
            if allowed is not None and key not in allowed:
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="warning",
                        message=(
                            f'unknown key "{key}" in [{section}]'
                            + _suggest(key, sorted(allowed))
                        ),
                    )
                )
                # Don't also type-check a key we don't recognize.
                continue

            # Type checks (conservative; only knowable cases).
            if key in integer_keys and (
                not isinstance(kval, int) or isinstance(kval, bool)
            ):
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            f"{section}.{key} must be an integer, got "
                            f"{type(kval).__name__}"
                        ),
                    )
                )
            elif (
                section == "model_improvement"
                and key in integer_keys
                and not (
                    _MODEL_IMPROVEMENT_FAMILY_MIN
                    <= kval
                    <= _MODEL_IMPROVEMENT_FAMILY_MAX
                )
            ):
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            f"{section}.{key} must be between "
                            f"{_MODEL_IMPROVEMENT_FAMILY_MIN} and "
                            f"{_MODEL_IMPROVEMENT_FAMILY_MAX}, got {kval!r}"
                        ),
                    )
                )
            elif key in numeric_keys and not _is_number(kval):
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            f"{section}.{key} must be a number, got "
                            f"{type(kval).__name__}"
                        ),
                    )
                )
            elif key in numeric_keys and not _is_valid_numeric_cap(kval):
                # A negative cap bricks every run (immediate BudgetExceeded); a
                # non-finite cap (TOML nan/inf) silently DISABLES enforcement.
                # Both pass an isinstance check but are never valid caps
                # (user-testing finding).
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            f"{section}.{key} must be a finite, non-negative "
                            f"number, got {kval!r}"
                        ),
                    )
                )
            elif (
                key in bool_keys or key in _UNIVERSAL_BOOL_KEYS
            ) and not isinstance(kval, bool):
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            f"{section}.{key} must be true or false, got "
                            f"{type(kval).__name__}"
                        ),
                    )
                )
            elif (
                section == "ai_media_provenance"
                and key == "verification_adapter"
                and kval != "c2patool"
            ):
                findings.append(
                    Finding(
                        section=section,
                        key=key,
                        severity="error",
                        message=(
                            "ai_media_provenance.verification_adapter must be "
                            'exactly "c2patool"'
                        ),
                    )
                )
            elif (
                section == "model_improvement"
                and key == "allow_cross_tenant"
                and kval is True
            ):
                findings.append(Finding(
                    section=section,
                    key=key,
                    severity="error",
                    message=(
                        "model_improvement.allow_cross_tenant is reserved and "
                        "unsupported; it must remain false"
                    ),
                ))
            elif (
                section == "model_improvement"
                and key == "require_signed_receipt"
                and kval is False
                and value.get("enable") is True
            ):
                findings.append(Finding(
                    section=section,
                    key=key,
                    severity="error",
                    message=(
                        "enabled model improvement requires "
                        "model_improvement.require_signed_receipt = true"
                    ),
                ))
            elif section == "ekko" and key == "provider_egress" and kval is True:
                findings.append(Finding(
                    section=section,
                    key=key,
                    severity="error",
                    message=(
                        "ekko.provider_egress is reserved and unsupported; "
                        "it must remain false"
                    ),
                ))

    findings.extend(_lint_governance_nested_tables(cfg))
    findings.extend(_lint_inline_secrets(cfg))
    findings.extend(_lint_group_mappings(cfg))
    return findings


def _lint_group_mappings(cfg: dict) -> list[Finding]:
    """Advise on the SCIM-group -> access mapping tables.

    ``[dashboard.group_roles]`` values must name a real dashboard role, and
    ``[dashboard.group_suites]`` values must name real department suites. The
    runtime already fails safe on a bad value (an unknown role/suite confers
    nothing rather than widening access), but a silent no-op is a nasty
    footgun, so surface the typo up front."""
    out: list[Finding] = []
    dash = cfg.get("dashboard")
    if not isinstance(dash, dict):
        return out
    roles = {"admin", "operator", "auditor", "viewer"}
    try:
        from .domain import SUITE_PREFIXES
        suites = set(SUITE_PREFIXES.values())
    except Exception:  # pragma: no cover - never let a lint import break config
        suites = set()

    gr = dash.get("group_roles")
    if isinstance(gr, dict):
        for group, role in gr.items():
            if not (isinstance(role, str) and role.strip().lower() in roles):
                out.append(Finding(
                    section="dashboard.group_roles", key=str(group),
                    severity="error",
                    message=(f"dashboard.group_roles[{group!r}] must name a role "
                             f"{sorted(roles)}, got {role!r}")))

    gs = dash.get("group_suites")
    if isinstance(gs, dict):
        for group, val in gs.items():
            vals = [val] if isinstance(val, str) else (
                list(val) if isinstance(val, (list, tuple)) else None)
            if vals is None:
                out.append(Finding(
                    section="dashboard.group_suites", key=str(group),
                    severity="error",
                    message=(f"dashboard.group_suites[{group!r}] must be a suite "
                             f"name or list of suite names, got {type(val).__name__}")))
                continue
            unknown = [s for s in vals if s not in suites] if suites else []
            if unknown:
                out.append(Finding(
                    section="dashboard.group_suites", key=str(group),
                    severity="warning",
                    message=(f"dashboard.group_suites[{group!r}] names unknown "
                             f"department(s) {unknown} (they confer no access)")))
    return out


def format_findings(findings: list[Finding]) -> str:
    """Render findings as a human-readable summary.

    Returns ``"config OK"`` when there are none. Otherwise one line per
    finding, errors before warnings, with a count header.
    """
    if not findings:
        return "config OK"

    ordered = sorted(
        findings,
        key=lambda f: (0 if f.severity == "error" else 1, f.section, f.key or ""),
    )
    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = len(findings) - n_err

    lines = [f"config: {n_err} error(s), {n_warn} warning(s)"]
    for f in ordered:
        loc = f.section if f.key is None else f"{f.section}.{f.key}"
        lines.append(f"  [{f.severity}] {loc}: {f.message}")
    return "\n".join(lines)


def warn_config_at_startup(*, strict: bool | None = None) -> list[Finding]:
    """Lint the active config and emit findings to the log at server startup.

    Surfaces typo'd sections/keys -- which silently fall back to defaults, so a
    ``[budget] max_dollarss`` runs UNCAPPED -- at the moment a long-running
    server starts, rather than only when an operator happens to run
    ``maverick config-lint``. Warn-only by default (operators may legitimately
    use keys newer than this build). With ``strict`` (or
    ``MAVERICK_CONFIG_STRICT=1``) an error-level finding raises ``SystemExit`` so
    a regulated deployment fails fast instead of running with a mis-typed
    control. Never raises on its own bugs -- config linting must not block a
    start except via the explicit strict gate."""
    import logging
    import os

    _log = logging.getLogger("maverick.config")
    try:
        from .config import _read_toml_raw, config_path
        p = config_path()
        if not p.exists():
            return []
        # Lint the RAW (uninterpolated) TOML, not load_config()'s env-expanded
        # output: the documented `api_key = "${ANTHROPIC_API_KEY}"` pattern
        # expands to the literal secret once the env var is set, which
        # _lint_inline_secrets would then flag as an inline secret -- and in
        # strict mode that false positive refuses to start the server. The raw
        # value keeps the "$..." form _looks_like_env_ref recognizes, and the
        # unknown-key checks still see the operator's literal keys.
        cfg = _read_toml_raw(p) or {}
        findings = lint_config(cfg)
    except Exception:  # pragma: no cover -- linting never blocks startup
        return []
    for f in findings:
        loc = f.section if f.key is None else f"{f.section}.{f.key}"
        _log.warning("config: [%s] %s: %s", f.severity, loc, f.message)
    if strict is None:
        strict = os.environ.get("MAVERICK_CONFIG_STRICT", "").strip().lower() in {
            "1", "true", "yes", "on",
        }
    # Strict mode fails on ANY finding, not just error-level ones: an unknown
    # key (a typo that silently uses a default -- the dangerous case M5 is
    # about) is reported as a *warning*, so an error-only gate would miss it. A
    # regulated/strict deployment wants a clean config or no start.
    if strict and findings:
        raise SystemExit(
            "config has problems (logged above) and MAVERICK_CONFIG_STRICT is "
            "set; refusing to start. Run `maverick config-lint` and fix them."
        )
    return findings
