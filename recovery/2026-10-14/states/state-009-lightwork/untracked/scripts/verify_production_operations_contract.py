#!/usr/bin/env python3
"""Verify source-owned operations controls without asserting live readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - release/CI installs the exact lock
    yaml = None

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))
from verify_service_objectives import (  # noqa: E402
    ServiceObjectiveError,
    validate_service_objectives,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = Path("release/production-operations-contract.json")
SURFACE = Path("release/production-surface-manifest.json")
OBJECTIVES = Path("release/service-objectives.json")
RULES = Path("deploy/observability/prometheus-rules.yaml")
DASHBOARD = Path("deploy/observability/grafana-production-overview.json")
RUNBOOK = Path("docs/enterprise/production-operations-runbook.md")
LEGACY_RUNBOOK = Path("docs/compliance/procedures/incident-response-runbook.md")

TOP_KEYS = {
    "schema",
    "profile_id",
    "source_version",
    "status",
    "verifier_failure_policy",
    "telemetry",
    "alerts",
    "incident_response",
    "emergency_controls",
    "capacity_and_cost",
    "audit_evidence_export",
    "support_and_escalation",
    "telemetry_pipeline",
    "source_bindings",
    "live_qualification_requirements",
}
ALERT_METADATA = {
    "severity",
    "owner",
    "summary",
    "description",
    "runbook_url",
    "evidence",
    "customer_impact",
}
SCENARIO_HEADINGS = {
    "authentication_or_credential_compromise": "## Authentication or credential compromise",
    "cross_tenant_exposure": "## Suspected cross-tenant exposure",
    "audit_or_evidence_outage": "## Audit or evidence pipeline outage",
    "sandbox_guardrail_or_policy_escape": "## Sandbox, guardrail, or policy escape",
    "malicious_plugin_or_excluded_integration": "## Malicious plugin or excluded integration",
    "provider_incident": "## Provider incident",
    "stuck_duplicate_or_indeterminate_action": "## Stuck, duplicate, or indeterminate action",
    "database_or_control_plane_loss": "## Database loss or control-plane authority failure",
    "bad_release": "## Bad release",
}
SEMANTIC_SHA256 = "8dba4787176b27df4c146a141b57cbfa9a36631ac7c7c329b2676706dc7287e3"
SOURCE_BINDING_PATHS = (
    "deploy/observability/grafana-production-overview.json",
    "deploy/observability/prometheus-rules.yaml",
    "deploy/observability/runbook.md",
    "docs/compliance/procedures/incident-response-runbook.md",
    "docs/enterprise/production-operations-runbook.md",
    "docs/env-vars.md",
    "packages/maverick-core/maverick/agent.py",
    "packages/maverick-core/maverick/audit/evidence.py",
    "packages/maverick-core/maverick/audit_evidence_verify.py",
    "packages/maverick-core/maverick/cli/__init__.py",
    "packages/maverick-core/maverick/logging_config.py",
    "packages/maverick-core/maverick/orchestrator.py",
    "packages/maverick-core/maverick/production_observability.py",
    "packages/maverick-core/tests/test_uncertainty_note.py",
    "packages/maverick-dashboard/maverick_dashboard/auth_metrics.py",
    "packages/maverick-dashboard/maverick_dashboard/health_routes.py",
    "packages/maverick-dashboard/maverick_dashboard/service_credentials.py",
    "packages/maverick-dashboard/maverick_dashboard/tenant_quota_health.py",
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_METRIC_RE = re.compile(r"\b(?:ALERTS|up|maverick_[A-Za-z0-9_:]+)\b")
_INERT_PROMQL_RE = re.compile(
    r"(?:[-+]?\d+(?:\.\d+)?|(?:vector|scalar)\s*\(\s*[-+]?\d+(?:\.\d+)?\s*\))",
    re.IGNORECASE,
)
PRODUCTION_ASSURANCE_METRICS = (
    "maverick_production_assurance_state",
    "maverick_production_assurance_value",
    "maverick_production_assurance_threshold",
    "maverick_production_assurance_events_total",
)

# (group, reviewed for duration or None for an intentional immediate alert,
# severity, owner, normalized PromQL SHA-256)
ALERT_SPECS = {
    "MaverickDown": (
        "maverick.availability",
        "2m",
        "critical",
        "site-reliability",
        "968580d8dad20b8aa748935a037e347c2824819eb52a58e573abe707e682557b",
    ),
    "MaverickConcurrencySaturated": (
        "maverick.availability",
        "10m",
        "warning",
        "site-reliability",
        "61f382a4fef4c8f7f48687022340dd2197a6c412adab927428cab54c4970ba67",
    ),
    "MaverickSpendSpike": (
        "maverick.cost",
        "15m",
        "warning",
        "finance-operations",
        "626700f58e45d8d94ae81e9c0cb2edee94e1da71df11f9d542ffa83faa375549",
    ),
    "MaverickGoalFailureRate": (
        "maverick.reliability",
        "30m",
        "warning",
        "service-operations",
        "6aaec432c78e5dfb9c80085bcc4040ef289ee6574155b373c954539cb5afb190",
    ),
    "MaverickJobQueueBacklog": (
        "maverick.capacity",
        "15m",
        "warning",
        "service-operations",
        "d1380ff13659ac9fd00434e52996e4d9ba29f7d11213cda39eee255e74ef4bd4",
    ),
    "MaverickJobQueueDeadLetter": (
        "maverick.capacity",
        "5m",
        "warning",
        "service-operations",
        "4cfe6ed6bf544cf6385e22feb2beb3775c5dfb8f699a5b35ef095f5bd6ef2678",
    ),
    "MaverickDiskLow": (
        "maverick.storage",
        "10m",
        "warning",
        "site-reliability",
        "eadb911b1bbc6f4218a49c00bf42f97cbbb685f2766d92fd860bbbe61e2fe55d",
    ),
    "MaverickAuthFailureSpike": (
        "maverick.security",
        "5m",
        "warning",
        "security-operations",
        "753bf276ce850bc3c8aa8e8b7c59de280d096fc2643a2e6729223c5626c8823c",
    ),
    "MaverickMetricsScrapeAuthFailure": (
        "maverick.security",
        None,
        "warning",
        "security-operations",
        "fc785876dc156fd3398f8b8137a57e87dc9593e81a911e2da87ac0a058457487",
    ),
    "MaverickTenantQuotaAuthorityUnavailable": (
        "maverick.tenant_capacity",
        "2m",
        "critical",
        "site-reliability",
        "bbdf615c4a40018e7d68a1edc390d16eb22ff4b5f14ac429e9639c989e037347",
    ),
    "MaverickTenantQuotaNearExhaustion": (
        "maverick.tenant_capacity",
        "15m",
        "warning",
        "capacity-management",
        "4005575bbf35e4d02540878553f8ec40dd93159d18c76b84f68356e44832b7e1",
    ),
    "MaverickProductionAssuranceFailed": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "site-reliability",
        "808edf99250a51b9d45fd98e54089b9c8f8643a36648223b4b9c01864e068e7f",
    ),
    "MaverickShieldControlFailure": (
        "maverick.production_assurance",
        None,
        "critical",
        "security-operations",
        "95febd130d3a4448a4b837d70f8002272b569228fc47ad984fba44f91eca5642",
    ),
    "MaverickDurableAuditRefusal": (
        "maverick.production_assurance",
        None,
        "critical",
        "security-operations",
        "0972fd797be99094917aee51506560af13ae7566e2272672a7361d69a49f0f26",
    ),
    "MaverickAuditForwarderFailure": (
        "maverick.production_assurance",
        None,
        "critical",
        "security-operations",
        "fb9c359966e3ef9d2441634227444181c5289b61cba15a2436a7f3a14afb0fae",
    ),
    "MaverickAuditForwarderBacklog": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "security-operations",
        "ac47a0e7f365f352ab4b1430b8607a9e806c0c4580ceefb15fd09693c1519299",
    ),
    "MaverickAuditAnchorStale": (
        "maverick.production_assurance",
        "5m",
        "critical",
        "security-operations",
        "ddbf41f2eadcaafed3cac2f394972a33259f8df195a17e346103e2b989641b1c",
    ),
    "MaverickKeyCustodyUnhealthy": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "security-operations",
        "4e4fda4ae5e6b82de14c827b83c55b650ec0019daf1bcdbee0d211971416beae",
    ),
    "MaverickControlPlaneLeaseLost": (
        "maverick.production_assurance",
        "1m",
        "critical",
        "site-reliability",
        "de99d15014c6dcc2b4cd6f287d8f4fda528a8e137cf7b17c285c83b4de80f6f6",
    ),
    "MaverickIsolationVerificationFailed": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "security-operations",
        "5630861b2df0e9b7271f50240e52f07378f96da9fde508116baf0f93dbcd0e55",
    ),
    "MaverickIndeterminateSideEffect": (
        "maverick.production_assurance",
        None,
        "critical",
        "service-operations",
        "c5fbdb3ec3e2378191661020a71044b62d0c1ce82926a177c641a0577dc32eef",
    ),
    "MaverickProductionQueueBacklog": (
        "maverick.production_assurance",
        "15m",
        "warning",
        "service-operations",
        "b68e16c2f6cef248224d708862135a907a1dfe726d55bfeeaee54b99ff59f759",
    ),
    "MaverickProductionQueueDeadLetter": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "service-operations",
        "34c8986c5890ec495a48bfefec3d9aedad09d3c86e9b48fd57dc4a2ea2ecc61d",
    ),
    "MaverickProductionQueueStaleLease": (
        "maverick.production_assurance",
        "2m",
        "critical",
        "service-operations",
        "7427354088efbea4a038227e33a2e143d39462d475bfc34c15b15868e26d9b35",
    ),
    "MaverickKillSwitchEngaged": (
        "maverick.production_assurance",
        None,
        "critical",
        "incident-command",
        "f094e6734f1c9e819684614ee44f9e3cbb4869cfcc85265e5447c06255b5536f",
    ),
    "MaverickPolicyFallback": (
        "maverick.production_assurance",
        None,
        "critical",
        "security-operations",
        "f214ad7fd46c478bfae38291b8d1eced6d6876fcda4bc99ded1b52c5e950d500",
    ),
}

DASHBOARD_PANELS = {
    1: ("Target availability", "stat", (("A", 'min(up{job="maverick"})'),)),
    2: (
        "Required production assurance",
        "stat",
        (("A", "min(maverick_production_assurance_state)"),),
    ),
    3: (
        "Concurrency utilization",
        "timeseries",
        (("A", "maverick_concurrent_goals / clamp_min(maverick_max_concurrent_goals, 1)"),),
    ),
    4: (
        "Durable queue standing",
        "timeseries",
        (
            (
                "A",
                'maverick_production_assurance_value{signal=~"queue_pending|queue_dead_letter|queue_stale_leases"}',
            ),
        ),
    ),
    5: (
        "Tenant quota remaining ratio (pseudonymous)",
        "timeseries",
        (
            (
                "A",
                'max by (tenant, resource) (maverick_tenant_quota_resource{standing="remaining"}) / clamp_min(max by (tenant, resource) (maverick_tenant_quota_resource{standing="limit"}), 1)',
            ),
        ),
    ),
    6: (
        "Inference spend rate",
        "timeseries",
        (("A", "rate(maverick_cost_dollars_total[15m]) * 3600"),),
    ),
    7: (
        "Goal outcomes",
        "timeseries",
        (("A", "sum by (status) (rate(maverick_goals_total[5m]))"),),
    ),
    8: (
        "Authentication refusals",
        "timeseries",
        (("A", "sum by (reason) (rate(maverick_auth_failures_total[5m]))"),),
    ),
    9: (
        "Audit pipeline",
        "timeseries",
        (
            (
                "A",
                'maverick_production_assurance_value{signal=~"audit_forwarder_backlog|audit_anchor_lag_seconds"}',
            ),
            (
                "B",
                'increase(maverick_production_assurance_events_total{signal=~"durable_audit_refusal|audit_forwarder_failure"}[5m])',
            ),
        ),
    ),
    10: ("Data-volume free bytes", "timeseries", (("A", "maverick_data_disk_free_bytes"),)),
    11: (
        "Metrics-service authentication health",
        "timeseries",
        (
            (
                "A",
                'increase(maverick_auth_failures_total{reason=~"metrics_bad_token|metrics_credential_unavailable|metrics_scope_violation"}[5m])',
            ),
        ),
    ),
    12: (
        "Firing Lightwork alerts",
        "table",
        (("A", 'ALERTS{alertstate="firing",alertname=~"Maverick.*"}'),),
    ),
}


class OperationsContractError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _path_label(path: Path, *, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load(path: Path, *, root: Path) -> dict[str, Any]:
    label = _path_label(path, root=root)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise OperationsContractError(f"{label} is not a non-empty regular file")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except OperationsContractError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise OperationsContractError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise OperationsContractError(f"{label} must be an object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _semantic_sha256(contract: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in contract.items()
        if key not in {"profile_id", "source_version", "source_bindings"}
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _source_sha256(path: Path, *, root: Path) -> str:
    resolved = path.resolve()
    if (
        root not in resolved.parents
        or path.is_symlink()
        or not resolved.is_file()
        or resolved.stat().st_size <= 0
    ):
        raise OperationsContractError(f"source binding is missing or unsafe: {path}")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OperationsContractError(f"source binding is not UTF-8: {path}") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _verify_source_bindings(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(SOURCE_BINDING_PATHS):
        raise OperationsContractError("operations source-binding inventory is incomplete")
    actual: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {
            "path",
            "sha256",
            "digest_mode",
        }:
            raise OperationsContractError(f"source_bindings[{index}] schema drift")
        path = raw.get("path")
        digest = raw.get("sha256")
        if (
            not isinstance(path, str)
            or path not in SOURCE_BINDING_PATHS
            or raw.get("digest_mode") != "utf8-lf-sha256"
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise OperationsContractError(f"source_bindings[{index}] is malformed")
        actual.append(path)
        if _source_sha256(root / path, root=root) != digest:
            raise OperationsContractError(f"operations source binding drifted: {path}")
    if tuple(actual) != SOURCE_BINDING_PATHS:
        raise OperationsContractError("operations source bindings are not in reviewed order")


def _text(path: Path, *, root: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OperationsContractError(f"cannot read {_path_label(path, root=root)}") from exc


def _nonempty_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise OperationsContractError(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise OperationsContractError(f"{label} contains an invalid item")
    if len(value) != len(set(value)):
        raise OperationsContractError(f"{label} contains duplicates")
    return value


def _promql_digest(expression: str) -> str:
    normalized = " ".join(expression.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_promql(expression: object, *, label: str) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise OperationsContractError(f"{label} has no PromQL expression")
    normalized = " ".join(expression.split())
    if (
        _INERT_PROMQL_RE.fullmatch(normalized)
        or _METRIC_RE.search(normalized) is None
        or any(ord(character) < 32 and character not in "\n\r\t" for character in expression)
    ):
        raise OperationsContractError(f"{label} has an inert or unsafe expression")
    stack: list[str] = []
    quote = False
    escaped = False
    pairs = {")": "(", "}": "{", "]": "["}
    for character in expression:
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quote = False
            continue
        if character == '"':
            quote = True
        elif character in "({[":
            stack.append(character)
        elif character in pairs:
            if not stack or stack.pop() != pairs[character]:
                raise OperationsContractError(f"{label} has unbalanced PromQL")
    if quote or stack:
        raise OperationsContractError(f"{label} has unbalanced PromQL")
    return expression


def _yaml_rules(text: str) -> Mapping[str, Any]:
    if yaml is None:
        raise OperationsContractError("PyYAML is required by the operations gate")

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        result: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as exc:
                raise OperationsContractError("unhashable YAML mapping key") from exc
            if duplicate:
                raise OperationsContractError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    try:
        document = yaml.load(text, Loader=UniqueKeyLoader)
    except OperationsContractError:
        raise
    except yaml.YAMLError as exc:
        raise OperationsContractError("Prometheus rules are not valid YAML") from exc
    if not isinstance(document, Mapping) or set(document) != {"groups"}:
        raise OperationsContractError("Prometheus rules root schema drift")
    return document


def _alert_inventory(text: str) -> dict[str, Mapping[str, Any]]:
    document = _yaml_rules(text)
    groups = document.get("groups")
    if not isinstance(groups, list) or not groups:
        raise OperationsContractError("Prometheus rule groups are missing")
    result: dict[str, Mapping[str, Any]] = {}
    group_names: set[str] = set()
    expected_annotations = ALERT_METADATA - {"severity", "owner"}
    for group in groups:
        if not isinstance(group, Mapping) or set(group) != {"name", "rules"}:
            raise OperationsContractError("Prometheus group schema drift")
        group_name = group.get("name")
        rules = group.get("rules")
        if (
            not isinstance(group_name, str)
            or not group_name
            or group_name in group_names
            or not isinstance(rules, list)
            or not rules
        ):
            raise OperationsContractError("Prometheus group identity/rules are invalid")
        group_names.add(group_name)
        for rule in rules:
            if not isinstance(rule, Mapping):
                raise OperationsContractError("Prometheus alert rule is not an object")
            name = rule.get("alert")
            if not isinstance(name, str) or name not in ALERT_SPECS or name in result:
                raise OperationsContractError(f"unknown or duplicate alert {name!r}")
            expected_group, expected_for, severity, owner, expression_digest = ALERT_SPECS[name]
            expected_keys = {"alert", "expr", "labels", "annotations"}
            if expected_for is not None:
                expected_keys.add("for")
            if set(rule) != expected_keys or group_name != expected_group:
                raise OperationsContractError(f"alert schema/group drift: {name}")
            if (rule.get("for") if "for" in rule else None) != expected_for:
                raise OperationsContractError(f"alert for-duration drift: {name}")
            expression = _validate_promql(rule.get("expr"), label=f"alert {name}")
            if _promql_digest(expression) != expression_digest:
                raise OperationsContractError(f"alert expression drift: {name}")
            labels = rule.get("labels")
            annotations = rule.get("annotations")
            if (
                not isinstance(labels, Mapping)
                or set(labels) != {"severity", "owner"}
                or labels.get("severity") != severity
                or labels.get("owner") != owner
                or not isinstance(annotations, Mapping)
                or set(annotations) != expected_annotations
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in annotations.values()
                )
            ):
                raise OperationsContractError(f"alert metadata drift: {name}")
            result[name] = rule
    if set(result) != set(ALERT_SPECS):
        raise OperationsContractError("Prometheus alert inventory is incomplete")
    return result


def _heading_slugs(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = match.group(1).strip().lower()
        heading = "".join(
            character
            for character in heading
            if not unicodedata.category(character).startswith("P") or character in "-_"
        )
        result.add(re.sub(r"\s", "-", heading))
    return result


def _validate_dashboard(document: Mapping[str, Any]) -> int:
    panels = document.get("panels")
    if not isinstance(panels, list) or len(panels) != len(DASHBOARD_PANELS):
        raise OperationsContractError("production dashboard panel inventory drift")
    seen: set[int] = set()
    for panel in panels:
        if not isinstance(panel, Mapping):
            raise OperationsContractError("production dashboard panel is malformed")
        panel_id = panel.get("id")
        if (
            not isinstance(panel_id, int)
            or isinstance(panel_id, bool)
            or panel_id not in DASHBOARD_PANELS
            or panel_id in seen
        ):
            raise OperationsContractError("production dashboard panel identity drift")
        seen.add(panel_id)
        title, panel_type, expected_targets = DASHBOARD_PANELS[panel_id]
        targets = panel.get("targets")
        if (
            panel.get("title") != title
            or panel.get("type") != panel_type
            or not isinstance(targets, list)
            or len(targets) != len(expected_targets)
        ):
            raise OperationsContractError(f"production dashboard panel contract drift: {panel_id}")
        actual_targets: list[tuple[object, object]] = []
        for target in targets:
            if not isinstance(target, Mapping):
                raise OperationsContractError(
                    f"production dashboard target is malformed: {panel_id}"
                )
            _validate_promql(
                target.get("expr"),
                label=f"dashboard panel {panel_id} target {target.get('refId')}",
            )
            actual_targets.append((target.get("refId"), target.get("expr")))
        if tuple(actual_targets) != expected_targets:
            raise OperationsContractError(f"production dashboard query drift: {panel_id}")
    if seen != set(DASHBOARD_PANELS):
        raise OperationsContractError("production dashboard is incomplete")
    return len(seen)


def verify(  # noqa: C901 - exact cross-file ops contract
    root: Path = ROOT,
) -> dict[str, object]:
    root = root.resolve()
    contract = _load(root / CONTRACT, root=root)
    surface = _load(root / SURFACE, root=root)
    if set(contract) != TOP_KEYS:
        raise OperationsContractError("operations-contract top-level schema drift")
    if contract["schema"] != "lightwork.production-operations-contract.v1":
        raise OperationsContractError("unsupported operations-contract schema")
    if contract["status"] != "source_controls_complete_live_operations_unqualified":
        raise OperationsContractError("operations contract must remain live-unqualified")
    if _semantic_sha256(contract) != SEMANTIC_SHA256:
        raise OperationsContractError(
            "operations reviewed semantics drifted; telemetry, alert, incident, "
            "capacity, evidence, and support statements require verifier review"
        )
    for field in ("profile_id", "source_version"):
        if contract[field] != surface.get(field):
            raise OperationsContractError(f"operations {field} does not match surface")

    policy = contract["verifier_failure_policy"]
    if not isinstance(policy, dict) or (
        policy.get("environment") != "MAVERICK_VERIFIER_REJECTION_POLICY"
        or policy.get("production_value") != "refuse"
        or policy.get("refusal_state") != "verification_refused"
        or policy.get("second_rejection") != "withhold_output"
        or policy.get("unknown_policy") != "fail_closed"
    ):
        raise OperationsContractError("verifier refusal policy drift")
    if set(_nonempty_list(policy.get("protected_scopes"), "protected scopes")) != {
        "production",
        "code",
        "tool_bound",
        "consequential",
    }:
        raise OperationsContractError("verifier protected-scope drift")

    evidence_paths: set[str] = set()
    for section_name in ("verifier_failure_policy", "telemetry", "audit_evidence_export"):
        section = contract[section_name]
        if not isinstance(section, dict):
            raise OperationsContractError(f"{section_name} must be an object")
        evidence_paths.update(
            _nonempty_list(section.get("code_evidence"), f"{section_name} evidence")
        )
    for value in evidence_paths:
        path = (root / value).resolve()
        if root not in path.parents or not path.is_file():
            raise OperationsContractError(f"missing/unsafe operations evidence: {value}")
    if not evidence_paths.issubset(SOURCE_BINDING_PATHS):
        raise OperationsContractError("operations evidence is not source bound")
    _verify_source_bindings(root, contract["source_bindings"])

    expected_alerts = set(_nonempty_list(contract["alerts"].get("expected"), "alerts"))
    if set(contract["alerts"].get("required_metadata") or []) != ALERT_METADATA:
        raise OperationsContractError("alert metadata contract drift")
    rules_text = _text(root / RULES, root=root)
    alert_inventory = _alert_inventory(rules_text)
    if set(alert_inventory) != expected_alerts:
        raise OperationsContractError("Prometheus alert inventory drift")
    runbook_urls = [str(rule["annotations"]["runbook_url"]) for rule in alert_inventory.values()]
    for value in runbook_urls:
        file_name, separator, anchor = value.partition("#")
        runbook_path = (root / file_name).resolve()
        if root not in runbook_path.parents or not runbook_path.is_file():
            raise OperationsContractError(f"alert runbook does not exist: {file_name}")
        if separator and anchor not in _heading_slugs(_text(runbook_path, root=root)):
            raise OperationsContractError(f"alert runbook anchor does not exist: {value}")
    if contract["alerts"].get("dashboard") != (
        "deploy/observability/grafana-production-overview.json"
    ):
        raise OperationsContractError("operations dashboard path drift")
    if contract["alerts"].get("paging_route") != "deployment_selected_and_live_test_required":
        raise OperationsContractError("paging route must remain live-qualified")
    dashboard = _load(root / DASHBOARD, root=root)
    dashboard_panels = _validate_dashboard(dashboard)

    runbook = _text(root / RUNBOOK, root=root)
    scenarios = set(
        _nonempty_list(
            contract["incident_response"].get("required_scenarios"), "incident scenarios"
        )
    )
    if scenarios != set(SCENARIO_HEADINGS):
        raise OperationsContractError("incident scenario inventory drift")
    runbook_paths = _nonempty_list(
        contract["incident_response"].get("runbooks"),
        "incident runbooks",
    )
    if not set(runbook_paths).issubset(SOURCE_BINDING_PATHS):
        raise OperationsContractError("incident runbooks are not source bound")
    for relative in runbook_paths:
        candidate = (root / relative).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise OperationsContractError(f"incident runbook is missing: {relative}")
    missing_headings = sorted(
        name for name, heading in SCENARIO_HEADINGS.items() if heading not in runbook
    )
    if missing_headings:
        raise OperationsContractError("runbook scenario missing: " + ", ".join(missing_headings))

    emergency = contract["emergency_controls"]
    if not isinstance(emergency, dict) or (
        emergency.get("halt") != "maverick halt --reason REASON"
        or emergency.get("clear") != "maverick unhalt"
        or emergency.get("live_drill_required") is not True
    ):
        raise OperationsContractError("emergency-control contract drift")
    legacy = _text(root / LEGACY_RUNBOOK, root=root)
    forbidden_commands = (
        '\ntouch "${MAVERICK_HALT_FILE',
        '\nrm -f "${MAVERICK_HALT_FILE',
        "\nkillswitch.clear()",
        "\nopen_world().disarm_halt()",
        "\nw.arm_halt(",
        "file trigger first",
    )
    if any(command in legacy for command in forbidden_commands):
        raise OperationsContractError("legacy runbook still instructs unsafe halt mutation")
    if "maverick halt --reason" not in legacy or "maverick unhalt" not in legacy:
        raise OperationsContractError("legacy runbook lacks reviewed halt commands")

    try:
        objective_summary = validate_service_objectives(root)
    except ServiceObjectiveError as exc:
        raise OperationsContractError(f"service-objective boundary is invalid: {exc}") from exc
    support = contract["support_and_escalation"]
    if support.get("targets_are_internal_not_sla") is not True:
        raise OperationsContractError("support targets must remain internal, not SLA")
    _nonempty_list(contract["live_qualification_requirements"], "live qualifications")

    agent_source = _text(
        root / "packages" / "maverick-core" / "maverick" / "agent.py",
        root=root,
    )
    for literal in ("MAVERICK_VERIFIER_REJECTION_POLICY", "verification_refused"):
        if literal not in agent_source:
            raise OperationsContractError(f"agent verifier evidence lacks {literal}")
    logging_source = _text(
        root / "packages" / "maverick-core" / "maverick" / "logging_config.py",
        root=root,
    )
    orchestrator_source = _text(
        root / "packages" / "maverick-core" / "maverick" / "orchestrator.py",
        root=root,
    )
    assurance_source = _text(
        root / "packages" / "maverick-core" / "maverick" / "production_observability.py",
        root=root,
    )
    for literal in (
        "_scrub_deep",
        "tenant_ref",
        "principal_ref",
        "service_version",
    ):
        if literal not in logging_source:
            raise OperationsContractError(f"structured logging lacks {literal}")
    if "channel=channel" not in orchestrator_source:
        raise OperationsContractError("goal execution does not bind channel correlation")
    for metric in PRODUCTION_ASSURANCE_METRICS:
        if metric not in assurance_source:
            raise OperationsContractError(
                f"production assurance metric implementation lacks {metric}"
            )
    auth_source = _text(
        root / "packages" / "maverick-dashboard" / "maverick_dashboard" / "auth_metrics.py",
        root=root,
    )
    credential_source = _text(
        root / "packages" / "maverick-dashboard" / "maverick_dashboard" / "service_credentials.py",
        root=root,
    )
    for reason in contract["telemetry_pipeline"].get("metrics_auth_reasons", []):
        if reason not in auth_source or reason not in credential_source:
            raise OperationsContractError(f"metrics auth vocabulary lacks {reason}")
    return {
        "schema": contract["schema"],
        "profile_id": contract["profile_id"],
        "source_version": contract["source_version"],
        "status": contract["status"],
        "alerts": len(alert_inventory),
        "dashboard_panels": dashboard_panels,
        "incident_scenarios": len(scenarios),
        "service_objectives_status": objective_summary["status"],
        "source_bindings": len(SOURCE_BINDING_PATHS),
        "live_qualification_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root)
    except OperationsContractError as exc:
        print(f"production-operations: REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
