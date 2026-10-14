"""Executable exclusions for surfaces outside the production manifest.

The source tree intentionally contains research and optional packages that are
not part of the first reviewed production profile.  A tool allowlist alone is
too late for those surfaces: plugin entry points may already have executed,
MCP processes may already have started, and an API or CLI may already have
advertised an unsupported feature.  This module is the small, dependency-light
gate shared by those earlier discovery and execution boundaries.

Development behavior is unchanged.  The gates become authoritative only when
``MAVERICK_REQUIRE_PRODUCTION_SURFACE`` explicitly selects a production
manifest.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from .production_scope import (
    ProductionManifest,
    ProductionScopeError,
    load_manifest,
    production_surface_required,
)


class ProductionFeatureExcludedError(RuntimeError):
    """An unsupported feature was invoked in explicit production mode."""


class ProductionConfigurationError(ProductionScopeError):
    """Configuration attempts to enable a manifest-excluded feature."""


# One feature label is used by runtime gates, CLI filtering, and dashboard
# visibility.  Requirements are deliberately conjunctive: a future manifest
# must include every relevant package/interface/feature declaration as well as
# the corresponding constraint before a dormant source-tree capability becomes
# production authority.
_FEATURES = frozenset(
    {
        "a2a",
        "attachments",
        "automated_retention",
        "channels",
        "code_dgm",
        "consequential_actions",
        "connectors",
        "evolve",
        "external_telemetry",
        "finance_suite",
        "flow_auto_apply",
        "flow_evolution",
        "flow_learning",
        "governed_records",
        "grpc_dispatch",
        "knowledge",
        "mcp",
        "notification_egress",
        "plugins",
        "privacy_suite",
        "queue_dispatch",
        "runtime_settings",
        "scim",
        "security_suite",
        "self_learning",
        "skills",
        "vendor_console",
        "voice",
        "weights_dgm",
    }
)

_MANIFEST_SURFACES = {
    "a2a": "interface:a2a-http",
    "attachments": "feature:attachments",
    "automated_retention": "feature:automated-retention",
    "channels": "feature:messaging-channels",
    "code_dgm": "feature:code-dgm",
    "consequential_actions": "feature:consequential-actions",
    "connectors": "feature:generated-connectors",
    "evolve": "package:maverick-evolve",
    "external_telemetry": "feature:external-telemetry-export",
    "finance_suite": "feature:finance-operations",
    "flow_auto_apply": "constraint:flow_auto_apply_enabled",
    "flow_evolution": "feature:code-dgm",
    "flow_learning": "feature:flow-learning-signals",
    "governed_records": "feature:governed-record-products",
    "grpc_dispatch": "runtime:grpc-worker-dispatch",
    "knowledge": "feature:knowledge-rag",
    "mcp": "interface:mcp-stdio/interface:mcp-http",
    "notification_egress": "feature:notification-egress",
    "plugins": "feature:third-party-plugins",
    "privacy_suite": "feature:privacy-operations",
    "queue_dispatch": "runtime:arq-worker-dispatch",
    "runtime_settings": "feature:runtime-settings",
    "scim": "identity:scim",
    "security_suite": "feature:security-operations",
    "self_learning": "feature:self-learning-ledger",
    "skills": "feature:user-skills",
    "vendor_console": "interface:vendor-console",
    "voice": "feature:voice",
    "weights_dgm": "feature:weights-dgm",
}


def _manifest_allows(manifest: ProductionManifest, feature: str) -> bool:
    if feature == "attachments":
        return bool(manifest.constraints.get("attachments_enabled")) and manifest.includes(
            "feature:attachments"
        )
    if feature == "knowledge":
        return bool(manifest.constraints.get("knowledge_enabled")) and all(
            manifest.includes(surface)
            for surface in ("package:maverick-knowledge", "feature:knowledge-rag")
        )
    if feature == "channels":
        return bool(manifest.constraints.get("channels")) and all(
            manifest.includes(surface)
            for surface in (
                "package:maverick-channels",
                "feature:messaging-channels",
            )
        )
    if feature == "connectors":
        return bool(manifest.constraints.get("connectors")) and manifest.includes(
            "feature:generated-connectors"
        )
    if feature == "plugins":
        return bool(manifest.constraints.get("plugins")) and all(
            manifest.includes(surface)
            for surface in (
                "feature:third-party-plugins",
                "package:npm:@maverick/plugin-sdk",
            )
        )
    if feature == "mcp":
        return manifest.includes("package:maverick-mcp-server") and any(
            manifest.includes(surface)
            for surface in ("interface:mcp-stdio", "interface:mcp-http")
        )
    if feature == "a2a":
        return manifest.includes("interface:a2a-http")
    if feature == "self_learning":
        return manifest.includes("feature:self-learning-ledger")
    if feature == "code_dgm":
        return bool(manifest.constraints.get("code_dgm_enabled")) and manifest.includes(
            "feature:code-dgm"
        )
    if feature == "evolve":
        return _manifest_allows(manifest, "code_dgm") and manifest.includes(
            "package:maverick-evolve"
        )
    if feature == "weights_dgm":
        return bool(manifest.constraints.get("weights_dgm_enabled")) and manifest.includes(
            "feature:weights-dgm"
        )
    if feature == "flow_evolution":
        # The current manifest has no independent auto-evolution constraint.
        # Treat self-rewriting flows as code-DGM authority, while ordinary
        # deterministic governed flows remain admitted.
        return _manifest_allows(manifest, "code_dgm")
    if feature == "flow_auto_apply":
        return _manifest_allows(manifest, "flow_evolution") and bool(
            manifest.constraints.get("flow_auto_apply_enabled")
        )
    if feature == "consequential_actions":
        return bool(manifest.constraints.get("consequential_actions_enabled")) and (
            manifest.includes("feature:consequential-actions")
        )
    if feature == "governed_records":
        return manifest.includes("feature:governed-record-products")
    if feature == "grpc_dispatch":
        return manifest.includes("runtime:grpc-worker-dispatch")
    if feature == "queue_dispatch":
        return manifest.includes("runtime:arq-worker-dispatch")
    if feature == "vendor_console":
        return manifest.includes("interface:vendor-console")
    simple_surfaces = {
        "automated_retention": "feature:automated-retention",
        "finance_suite": "feature:finance-operations",
        "external_telemetry": "feature:external-telemetry-export",
        "flow_learning": "feature:flow-learning-signals",
        "notification_egress": "feature:notification-egress",
        "privacy_suite": "feature:privacy-operations",
        "runtime_settings": "feature:runtime-settings",
        "scim": "identity:scim",
        "security_suite": "feature:security-operations",
        "skills": "feature:user-skills",
        "voice": "feature:voice",
    }
    if feature in simple_surfaces:
        return manifest.includes(simple_surfaces[feature])
    raise ValueError(f"unknown production feature: {feature!r}")


def feature_allowed(feature: str) -> bool:
    """Whether ``feature`` is admitted by the active production manifest."""
    if feature not in _FEATURES:
        raise ValueError(f"unknown production feature: {feature!r}")
    if not production_surface_required():
        return True
    return _manifest_allows(load_manifest(), feature)


def require_feature(feature: str, *, action: str | None = None) -> None:
    """Raise before an excluded feature can import, discover, or execute."""
    if feature_allowed(feature):
        return
    description = action or feature.replace("_", " ")
    raise ProductionFeatureExcludedError(
        f"{description} is outside the reviewed production surface "
        f"(excluded manifest surface {_MANIFEST_SURFACES[feature]})"
    )


def _table(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name)
    return value if isinstance(value, Mapping) else {}


_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "enable", "enabled", "y", "t"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def _enabled_value(value: object) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_WORDS:
            return True
        if not normalized or normalized in _FALSE_WORDS:
            return False
    # Invalid high-authority values are handled by the owning strict config
    # parser.  They are not silently interpreted as an affirmative grant here.
    return False


def _env_enabled(name: str) -> bool:
    if name not in os.environ:
        return False
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if not raw or raw in _FALSE_WORDS:
        return False
    raise ProductionConfigurationError(f"{name} must be an explicit boolean value")


def _nonempty(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return bool(value)
    return True


def _enabled_channel_names(config: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for name, value in _table(config, "channels").items():
        if name == "routing" or not isinstance(value, Mapping):
            continue
        if _enabled_value(value.get("enabled")):
            names.append(str(name))
    return sorted(names)


def _enabled_mcp_names(config: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for name, value in _table(config, "mcp_servers").items():
        if not isinstance(value, Mapping):
            continue
        # MCP servers default to enabled once a transport is configured.
        if value.get("enabled") is not False and (
            _nonempty(value.get("command")) or _nonempty(value.get("url"))
        ):
            names.append(str(name))
    return sorted(names)


def _plugin_configuration_enabled(config: Mapping[str, Any]) -> bool:
    plugins = _table(config, "plugins")
    return any(
        _nonempty(plugins.get(key))
        for key in ("enabled", "ts", "grpc", "installable")
    )


def production_configuration_violations(  # noqa: C901 - explicit authority inventory
    config: Mapping[str, Any],
    *,
    include_runtime_overrides: bool = True,
) -> tuple[str, ...]:
    """Return configured authorities that conflict with the active manifest."""
    if not production_surface_required():
        return ()
    if not isinstance(config, Mapping):
        raise ProductionConfigurationError("production configuration root must be a table")
    manifest = load_manifest()
    violations: list[str] = []

    def denied(feature: str) -> bool:
        return not _manifest_allows(manifest, feature)

    knowledge = _table(config, "knowledge")
    if denied("knowledge") and (
        _enabled_value(knowledge.get("enable"))
        or _enabled_value(knowledge.get("enabled"))
        or _env_enabled("MAVERICK_KNOWLEDGE_ENABLED")
    ):
        violations.append("knowledge/RAG is enabled")

    channels = _enabled_channel_names(config)
    if denied("channels") and channels:
        violations.append("messaging channels are enabled: " + ", ".join(channels))

    connector_cfg = _table(config, "governed_connectors")
    event_triggers = _table(config, "event_triggers")
    automation_import = _table(config, "automation_import")
    if denied("connectors") and (
        _enabled_value(connector_cfg.get("enable"))
        or _env_enabled("MAVERICK_GOVERNED_CONNECTORS")
        or _enabled_value(event_triggers.get("enable"))
        or _env_enabled("MAVERICK_EVENT_TRIGGERS")
        or _enabled_value(automation_import.get("enable"))
        or _env_enabled("MAVERICK_AUTOMATION_IMPORT")
    ):
        violations.append(
            "generated/governed connectors or outbound automation sources are enabled"
        )

    plugin_env = os.environ.get("MAVERICK_PLUGINS_ALLOW", "").strip()
    if denied("plugins") and (plugin_env or _plugin_configuration_enabled(config)):
        violations.append("third-party plugins are enabled")

    mcp_names = _enabled_mcp_names(config)
    if denied("mcp") and mcp_names:
        violations.append("MCP servers are enabled: " + ", ".join(mcp_names))
    if denied("mcp") and _env_enabled("MAVERICK_ALLOW_MCP_ACQUISITION"):
        violations.append("self-learning MCP acquisition is enabled")

    a2a = _table(config, "a2a")
    if denied("a2a") and (
        _enabled_value(a2a.get("enabled")) or _env_enabled("MAVERICK_A2A_ENABLED")
    ):
        violations.append("A2A HTTP is enabled")

    self_learning = _table(config, "self_learning")
    self_improvement = _table(config, "self_improvement")
    self_harness = _table(config, "self_harness")
    dreaming = _table(config, "dreaming")
    reflexion = _table(config, "reflexion")
    if denied("self_learning"):
        learning_controls = (
            _enabled_value(self_learning.get("enable"))
            or _env_enabled("MAVERICK_SELF_LEARNING")
            or _enabled_value(self_improvement.get("enable"))
            or _env_enabled("MAVERICK_SELF_IMPROVEMENT")
            or _enabled_value(self_harness.get("enable"))
            or _env_enabled("MAVERICK_SELF_HARNESS")
            or _enabled_value(dreaming.get("enable"))
            or _env_enabled("MAVERICK_DREAMING")
            or _enabled_value(reflexion.get("enable"))
            or _env_enabled("MAVERICK_REFLEXION")
            or _enabled_value(self_improvement.get("factory_learning"))
            or _env_enabled("MAVERICK_FACTORY_LEARNING")
        )
        if learning_controls:
            violations.append(
                "self-improving learning loops are enabled "
                "(excluded manifest surface feature:self-learning-ledger)"
            )

    self_modify = _table(config, "self_modify")
    if denied("code_dgm") and (
        _enabled_value(self_modify.get("enable"))
        or _env_enabled("MAVERICK_SELF_MODIFY")
    ):
        violations.append(
            "code DGM is enabled (excluded manifest surface feature:code-dgm)"
        )

    adapter = _table(config, "adapter_rung")
    model_improvement = _table(config, "model_improvement")
    if denied("weights_dgm") and (
        _enabled_value(adapter.get("enable"))
        or _enabled_value(model_improvement.get("enable"))
    ):
        violations.append(
            "weights DGM/model improvement is enabled "
            "(excluded manifest surface feature:weights-dgm)"
        )

    if denied("governed_records"):
        governed_product_sections = (
            "evidence_graph",
            "model_risk_assurance",
            "evidence_gateway",
            "finance_operations",
        )
        enabled_products = [
            name
            for name in governed_product_sections
            if _enabled_value(_table(config, name).get("enable"))
        ]
        if _env_enabled("MAVERICK_EVIDENCE_GATEWAY"):
            enabled_products.append("evidence_gateway")
        if enabled_products:
            violations.append(
                "governed-record product suites are enabled: "
                + ", ".join(sorted(set(enabled_products)))
            )

    flows = _table(config, "flows")
    if denied("flow_evolution") and (
        _enabled_value(flows.get("auto_evolve"))
        or _env_enabled("MAVERICK_FLOWS_AUTO")
    ):
        violations.append("automatic flow evolution is enabled")
    if denied("flow_auto_apply") and _enabled_value(flows.get("auto_apply")):
        violations.append("flow auto-apply is enabled")

    if denied("consequential_actions") and _enabled_value(
        _table(config, "env_hunt").get("response_execution")
    ):
        violations.append("consequential environment-response execution is enabled")

    if denied("finance_suite") and _enabled_value(
        _table(config, "finance_operations").get("enable")
    ):
        violations.append("finance operations are enabled")

    if denied("security_suite") and _enabled_value(
        _table(config, "security_ops").get("enable")
    ):
        violations.append("security operations are enabled")

    if denied("privacy_suite") and _enabled_value(
        _table(config, "privacy_ops").get("enable")
    ):
        violations.append("privacy operations are enabled")

    voice_configured = _enabled_value(
        _table(config, "voice").get("dashboard_commands")
    ) or _env_enabled("MAVERICK_VOICE_COMMANDS")
    if denied("voice") and voice_configured:
        violations.append("dashboard voice commands are enabled")

    skills = _table(config, "skills")
    if denied("skills") and (
        _enabled_value(skills.get("builtin"))
        or _nonempty(skills.get("catalogs"))
    ):
        violations.append("runtime or catalog skills are enabled")

    if denied("scim") and _nonempty(os.environ.get("MAVERICK_SCIM_TOKEN")):
        violations.append("SCIM identity provisioning is configured")

    license_cfg = _table(config, "license")
    if denied("vendor_console") and (
        _nonempty(license_cfg.get("api_url"))
        or _nonempty(os.environ.get("MAVERICK_LICENSE_API"))
    ):
        violations.append("connected entitlement vendor-console polling is configured")

    grpc_dispatch = _table(config, "grpc_dispatch")
    if denied("grpc_dispatch") and (
        _nonempty(grpc_dispatch.get("target"))
        or _nonempty(os.environ.get("MAVERICK_GRPC_DISPATCH_TARGET"))
    ):
        violations.append("legacy gRPC remote-worker dispatch is configured")

    queue = _table(config, "queue")
    queue_network_keys = (
        "backend",
        "namespace",
        "redis_dsn",
        "redis_ca_certs",
        "redis_certfile",
        "redis_keyfile",
        "signing_key",
    )
    queue_network_env = (
        "MAVERICK_QUEUE_NAMESPACE",
        "MAVERICK_QUEUE_REDIS_CA_CERTS",
        "MAVERICK_QUEUE_REDIS_CERTFILE",
        "MAVERICK_QUEUE_REDIS_DSN",
        "MAVERICK_QUEUE_REDIS_KEYFILE",
        "MAVERICK_QUEUE_SIGNING_KEY",
    )
    if denied("queue_dispatch") and (
        any(_nonempty(queue.get(key)) for key in queue_network_keys)
        or any(_nonempty(os.environ.get(name)) for name in queue_network_env)
        or _env_enabled("MAVERICK_ALLOW_INSECURE_QUEUE_REDIS")
    ):
        violations.append("ARQ/Redis remote-worker dispatch is configured")

    notifications = _table(config, "notifications")
    notification_keys = (
        "backend",
        "ntfy_topic",
        "ntfy_server",
        "pushover_user_key",
        "pushover_app_token",
        "discord_webhook",
        "slack_webhook",
        "batch_window_seconds",
        "batch_max",
    )
    notification_env = (
        "DISCORD_NOTIFY_WEBHOOK_URL",
        "MAVERICK_NTFY_TOPIC",
        "MAVERICK_NOTIFY_BATCH_MAX",
        "MAVERICK_NOTIFY_BATCH_WINDOW",
        "PUSHOVER_APP_TOKEN",
        "PUSHOVER_USER_KEY",
        "SLACK_NOTIFY_WEBHOOK_URL",
    )
    alerting = _enabled_value(_table(config, "alerts").get("enabled")) or _env_enabled(
        "MAVERICK_ALERTS"
    )
    configured_backend = str(notifications.get("backend") or "").strip().lower()
    notification_values = any(
        _nonempty(notifications.get(key))
        for key in notification_keys
        if key != "backend"
    )
    if denied("notification_egress") and (
        configured_backend not in {"", "none"}
        or notification_values
        or any(_nonempty(os.environ.get(name)) for name in notification_env)
        or alerting
    ):
        violations.append("notification/webhook egress is configured")

    observability = _table(config, "observability")
    legacy_telemetry_env = sorted(
        name
        for name, value in os.environ.items()
        if name.startswith(
            ("MAVERICK_OTEL_", "MAVERICK_PROMETHEUS_", "MAVERICK_SENTRY_")
        )
        and _nonempty(value)
    )
    if denied("external_telemetry") and (
        _nonempty(observability.get("sentry_dsn")) or legacy_telemetry_env
    ):
        violations.append(
            "legacy external telemetry exporters/listeners are configured"
        )

    if denied("automated_retention") and _nonempty(_table(config, "retention")):
        violations.append("automated destructive retention is configured")

    # [[hooks]] commands are arbitrary host subprocesses. The first production
    # profile disables command execution and does not define a reviewed hook
    # runtime, so configured commands cannot be treated as harmless lifecycle
    # metadata even though third-party plugin discovery is also disabled.
    if _nonempty(config.get("hooks")):
        violations.append("configured shell hooks are enabled")

    if include_runtime_overrides:
        try:
            from .runtime_overrides import mcp_overlay, plugin_overlay

            plugin_on, _plugin_off = plugin_overlay()
            if denied("plugins") and plugin_on:
                violations.append("dashboard runtime plugin enablement is present")
            runtime_mcp = mcp_overlay()
            if denied("mcp") and runtime_mcp:
                violations.append("dashboard runtime MCP servers are enabled")
        except ProductionConfigurationError:
            raise
        except Exception as exc:
            # Production cannot prove that an out-of-band authority store is
            # empty when the store itself is unavailable or malformed.
            raise ProductionConfigurationError(
                "production extension overrides could not be inspected"
            ) from exc

    return tuple(dict.fromkeys(violations))


def validate_production_configuration(
    config: Mapping[str, Any] | None = None,
    *,
    include_runtime_overrides: bool = True,
) -> tuple[str, ...]:
    """Fail startup when configuration arms a manifest-excluded surface."""
    if not production_surface_required():
        return ()
    if config is None:
        try:
            from .config import load_config

            config = load_config() or {}
        except Exception as exc:
            raise ProductionConfigurationError(
                "production configuration could not be loaded"
            ) from exc
    try:
        from .config import config_source_errors

        source_errors = config_source_errors()
    except Exception as exc:
        raise ProductionConfigurationError(
            "production configuration source health could not be verified"
        ) from exc
    if source_errors:
        source_names = ", ".join(
            sorted({os.path.basename(path) or "configuration" for path in source_errors})
        )
        raise ProductionConfigurationError(
            "production configuration has unreadable or malformed active source(s): "
            + source_names
        )
    violations = production_configuration_violations(
        config,
        include_runtime_overrides=include_runtime_overrides,
    )
    if violations:
        raise ProductionConfigurationError(
            "production configuration enables excluded surfaces: "
            + "; ".join(violations)
        )
    return violations


_CLI_FEATURES: dict[str, str] = {
    "ai-act": "consequential_actions",
    "analytics": "self_learning",
    "assess": "governed_records",
    "billing": "finance_suite",
    "budget-tune": "self_learning",
    "calibrate": "self_learning",
    "canary": "self_learning",
    "clause-playbook": "privacy_suite",
    "codec-learn": "self_learning",
    "codec-probe": "self_learning",
    "compliance": "security_suite",
    "compounding": "self_learning",
    "config": "runtime_settings",
    "connectors": "connectors",
    "controls": "security_suite",
    "cost-retro": "self_learning",
    "debate": "self_learning",
    "demo": "self_learning",
    "dpia": "privacy_suite",
    "dream": "self_learning",
    "dsar": "privacy_suite",
    "earned-autonomy": "self_learning",
    "ekko": "self_learning",
    "erase": "privacy_suite",
    "erase-verify": "privacy_suite",
    "export-user": "privacy_suite",
    "factory-learn": "self_learning",
    "failures": "self_learning",
    "finance": "finance_suite",
    "fleet-memory": "self_learning",
    "flywheel": "self_learning",
    "hindsight": "self_learning",
    "hunt": "security_suite",
    "insights-export": "self_learning",
    "insights-import": "self_learning",
    "init": "runtime_settings",
    "knowledge": "knowledge",
    "learn-demo": "self_learning",
    "learned": "self_learning",
    "mcp": "mcp",
    "mcp-registry": "mcp",
    "migrate": "runtime_settings",
    "model-improvement": "weights_dgm",
    "onboard": "runtime_settings",
    "overrides": "runtime_settings",
    "plan-reflect": "self_learning",
    "plugin": "plugins",
    "prove-learning": "self_learning",
    "record-outcome": "flow_learning",
    "remediate": "consequential_actions",
    "report-issue": "consequential_actions",
    "retention": "automated_retention",
    "reward-model": "weights_dgm",
    "ropa": "privacy_suite",
    "savings": "finance_suite",
    "self-harness": "self_learning",
    "self-modify": "code_dgm",
    "security": "security_suite",
    "serve": "channels",
    "skill": "skills",
    "skills": "skills",
    "soc2": "security_suite",
    "tax": "finance_suite",
    "voice": "voice",
    "workforce-graduation": "self_learning",
}

# The first profile intentionally exposes only deployment, lifecycle, core
# goal/flow operation, and evidence-inspection commands. This is an explicit
# registry rather than an inference from source-tree presence. New commands
# are denied until added to one of these declarations.
_PRODUCTION_CORE_CLI_COMMANDS = frozenset(
    {
        "airgap",
        "answer",
        "attest",
        "audit",
        "budget",
        "capability",
        "chat",
        "client",
        "confidential-compute",
        "config-lint",
        "cost",
        "costs",
        "dashboard",
        "diag",
        "doctor",
        "encryption",
        "enterprise",
        "fact",
        "facts",
        "forward",
        "governance",
        "halt",
        "history",
        "logs",
        "monitor",
        "operator-contract",
        "preflight",
        "production-onboarding-readiness",
        "proof",
        "ps",
        "record",
        "release-runtime-check",
        "resume",
        "rewind",
        "runs",
        "safety",
        "schema-plan",
        "spend",
        "start",
        "status",
        "support",
        "tenant",
        "unhalt",
        "version",
        "whoami",
    }
)

_PRODUCTION_UNSUPPORTED_CLI_COMMANDS = frozenset(
    {
        "cache",
        "backup",
        "charts",
        "codebook",
        "domains-audit",
        "domains-eval",
        "domains-lint",
        "donate",
        "export",
        "fleet",
        "gc",
        "gen-stubs",
        "graph",
        "import",
        "local-runtime",
        "memory-plane",
        "queue",
        "schedule",
        "share",
        "template",
        "trust",
        "watch",
        "worker",
    }
)

# Core groups are admitted one operation at a time.  A reviewed top-level
# group is not authority for every present or future child command: otherwise
# an unsafe command added below ``tenant`` or ``encryption`` silently bypasses
# the default-deny registry.
_PRODUCTION_CORE_CLI_SUBCOMMANDS = frozenset(
    {
        "attest export",
        "attest export-verifier",
        "attest key",
        "attest verify",
        "audit checkpoint",
        "audit checkpoint publish",
        "audit checkpoint retire",
        "audit checkpoint verify",
        "audit evidence",
        "audit evidence-verify",
        "audit export",
        "audit forward",
        "audit grep",
        "audit seal",
        "audit siem-outbox",
        "audit siem-outbox drain",
        "audit siem-outbox redrive",
        "audit siem-outbox status",
        "audit tail",
        "audit verify",
        "audit worm",
        "audit worm push",
        "audit worm verify",
        "capability revocations",
        "capability revoke",
        "client erase",
        "client export",
        "client status",
        "diag circuits",
        "diag cost-by-tag",
        "diag health",
        "diag ratelimits",
        "diag replay",
        "encryption migrate-postgres",
        "enterprise verify",
        "governance check",
        "governance impact",
        "governance lineage",
        "governance show",
        "record search",
        "record stats",
        "record verify",
        "tenant backfill",
        "tenant list",
        "tenant postgres-erase",
        "tenant postgres-export",
        "tenant postgres-inventory",
        "tenant rls-apply",
        "tenant rls-preflight",
        "tenant suspend",
    }
)

_PRODUCTION_UNSUPPORTED_CLI_SUBCOMMANDS = frozenset(
    {
        # These mint or consume host-local private key material. The first
        # production profile requires externally custodied audit and Vault
        # wrapping keys, so their local rotation workflows are not authority.
        "audit rotate-key",
        # These expand production authority through host-local JSON registries
        # without a shared, crash-safe authorization/CAS receipt. Emergency
        # restriction operations (tenant suspend and capability revoke) remain
        # admitted and commit the restriction before their required audit.
        "capability unrevoke",
        "tenant create",
        "tenant quota",
        "tenant resume",
        "tenant kms-rotate",
        # These operate on the legacy local SQLite/process key plane.  The
        # first production profile is shared Postgres plus external KMS.
        "encryption backup-key",
        "encryption migrate",
        "encryption rotate",
        # This capsule includes local learned-state sidecars, which the first
        # profile excludes even though offline verification remains useful.
        "record export",
        # Registry deletion/purge does not erase the shared Postgres closure;
        # use the reviewed tenant/client Postgres lifecycle commands instead.
        "tenant delete",
    }
)


def _normalize_cli_command(command: str) -> str:
    return " ".join(str(command or "").split())


def _inherited_cli_classification(name: str) -> str | None:
    """Inherit a hidden top-level feature/unsupported group classification."""
    if " " not in name:
        return None
    ancestor = name.split(" ", 1)[0]
    feature = _CLI_FEATURES.get(ancestor)
    if feature is not None:
        return f"feature:{feature}"
    if ancestor in _PRODUCTION_UNSUPPORTED_CLI_COMMANDS:
        return "unsupported"
    return None


def cli_command_feature(command: str) -> str | None:
    """Manifest feature governing a top-level CLI command, if any."""
    name = _normalize_cli_command(command)
    feature = _CLI_FEATURES.get(name)
    if feature is not None:
        return feature
    if " " in name:
        return _CLI_FEATURES.get(name.split(" ", 1)[0])
    return None


def cli_command_classification(command: str) -> str | None:
    """Return the reviewed production classification for one CLI command."""
    name = _normalize_cli_command(command)
    if name in _PRODUCTION_CORE_CLI_COMMANDS or name in _PRODUCTION_CORE_CLI_SUBCOMMANDS:
        return "core"
    feature = _CLI_FEATURES.get(name)
    if feature is not None:
        return f"feature:{feature}"
    if (
        name in _PRODUCTION_UNSUPPORTED_CLI_COMMANDS
        or name in _PRODUCTION_UNSUPPORTED_CLI_SUBCOMMANDS
    ):
        return "unsupported"
    return _inherited_cli_classification(name)


def cli_command_allowed(command: str) -> bool:
    if not production_surface_required():
        return True
    name = _normalize_cli_command(command)
    if name in _PRODUCTION_CORE_CLI_COMMANDS or name in _PRODUCTION_CORE_CLI_SUBCOMMANDS:
        return True
    feature = cli_command_feature(name)
    return feature is not None and feature_allowed(feature)


__all__ = [
    "ProductionConfigurationError",
    "ProductionFeatureExcludedError",
    "cli_command_allowed",
    "cli_command_classification",
    "cli_command_feature",
    "feature_allowed",
    "production_configuration_violations",
    "require_feature",
    "validate_production_configuration",
]
