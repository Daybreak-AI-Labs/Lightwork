"""Governed, read-only discovery for the Lightwork agent trust estate.

The auditor deliberately separates what operators *declare* from what runtime
and configuration sources *observe*.  Discovery projects existing authority;
it never edits the agent-trust registry, MCP configuration, provider settings,
or tool policy.  Human metadata and lifecycle changes live in a separate CAS
store, while every observation is bound into :mod:`maverick.evidence_graph`.

OpenTelemetry ingestion accepts only a small semantic-metadata allowlist.
Prompts, messages, tool arguments, tool results, request/response bodies, and
arbitrary attributes are dropped before persistence.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

_OBSERVED = GovernedRecordStore(
    "agent_trust_observed",
    "ATO",
    "agent_trust_observed_asset",
)
_DECLARED = GovernedRecordStore(
    "agent_trust_declared",
    "ATD",
    "agent_trust_declared_asset",
)

_OBSERVED_SCHEMA = "lightwork.agent-trust-observed-asset.v1"
_DECLARED_SCHEMA = "lightwork.agent-trust-declared-asset.v1"
_PACK_SCHEMA = "lightwork.agent-trust-evidence-pack.v1"
_PACK_EVENT = "agent_trust_evidence_pack"

_SOURCE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z")
_ENTITY_RE = re.compile(r"ATE-[0-9a-f]{32}\Z")
_TRACE_RE = re.compile(r"[0-9a-f]{32}\Z")
_SPAN_RE = re.compile(r"[0-9a-f]{16}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CONTENT_KEY_RE = re.compile(
    r"(?:prompt|completion|message|content|body|argument|result|input|output)",
    re.IGNORECASE,
)

_OTEL_ALLOWED = frozenset({
    "deployment.environment.name",
    "gen_ai.agent.id",
    "gen_ai.agent.name",
    "gen_ai.operation.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.system",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "service.name",
    "service.namespace",
})
_MAX_OTEL_SPANS = 1000
_MAX_OTEL_ATTRIBUTES = 256
_MAX_OBSERVATIONS_PER_ASSET = 64
_MAX_TOOL_INVENTORY = 5000
_MAX_FUTURE_SKEW_SECONDS = 300
_DEFAULT_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60
_LIFECYCLES = frozenset({
    "proposed",
    "rejected",
    "approved",
    "production",
    "suspended",
    "retired",
})
_LIFECYCLE_TRANSITIONS = {
    "proposed": frozenset({"approved", "rejected", "retired"}),
    "rejected": frozenset(),
    "approved": frozenset({"production", "suspended", "retired"}),
    "production": frozenset({"suspended", "retired"}),
    "suspended": frozenset({"production", "retired"}),
    "retired": frozenset(),
}
_ACTIVE_POLICY_LIFECYCLES = frozenset({"approved", "production"})
_RISK_TIERS = frozenset({"low", "medium", "high", "critical"})
_OWNABLE_TYPES = frozenset({"agent", "mcp_server", "provider"})
_MISSING = object()


class AgentTrustAuditorError(RuntimeError):
    """Base failure for the agent trust auditor."""


class AgentTrustAuditorDisabled(AgentTrustAuditorError):
    """The deployment-global auditor gate is not enabled."""


class AgentTrustAuditorConfigError(AgentTrustAuditorError):
    """The auditor cannot trust active configuration."""


class IdentityCollisionError(AgentTrustAuditorError):
    """A content-derived identity is already bound to another source record."""


class AgentTrustAuditorStateError(AgentTrustAuditorError):
    """Stored auditor evidence is incomplete or inconsistent."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _required_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{label} contains control characters")
    return text


def _optional_text(value: object, label: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    text = value.strip()
    if len(text) > limit:
        raise ValueError(f"{label} exceeds {limit} characters")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{label} contains control characters")
    return text


def _source(value: object) -> str:
    source = _required_text(value, "source", 64)
    if not _SOURCE_RE.fullmatch(source):
        raise ValueError("source must be a stable namespace token")
    return source


def _source_id(value: object) -> str:
    return _required_text(value, "source_id", 256)


def _timestamp(value: float | None) -> float:
    stamp = time.time() if value is None else float(value)
    if not math.isfinite(stamp) or stamp <= 0:
        raise ValueError("observed_at must be a positive finite timestamp")
    return stamp


def _not_future(stamp: float, reference: float, *, label: str) -> float:
    if stamp > reference + _MAX_FUTURE_SKEW_SECONDS:
        raise ValueError(f"{label} exceeds the clock-skew allowance")
    return stamp


def _identity_digest(source: str, source_id: str) -> str:
    """Full binding digest; kept as a seam for explicit collision tests."""
    return _sha256({"source": source, "source_id": source_id})


def stable_entity_id(source: str, source_id: str) -> str:
    """Return a stable identity derived from the exact source namespace pair."""
    namespace = _source(source)
    record_id = _source_id(source_id)
    return f"ATE-{_identity_digest(namespace, record_id)[:32]}"


def _entity_id(value: object) -> str:
    entity = _required_text(value, "entity_id", 36)
    if not _ENTITY_RE.fullmatch(entity):
        raise ValueError("entity_id is not a Lightwork agent-estate identity")
    return entity


def _record_id(prefix: str, entity_id: str) -> str:
    digest = hashlib.sha256(entity_id.encode("ascii")).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _name_list(values: object, label: str, *, limit: int = 512) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise ValueError(f"{label} must be a list")
    raw = list(islice(iter(values), limit + 1))
    if len(raw) > limit:
        raise ValueError(f"{label} exceeds {limit} items")
    return sorted({_required_text(value, label, 256) for value in raw})


def _settings() -> dict[str, Any]:
    try:
        from .config import config_source_errors, load_global_config

        cfg = load_global_config()
        if config_source_errors(include_tenant=False):
            raise AgentTrustAuditorConfigError(
                "agent trust auditor configuration is unreadable"
            )
    except AgentTrustAuditorConfigError:
        raise
    except Exception as exc:
        raise AgentTrustAuditorConfigError(
            "agent trust auditor configuration is unavailable"
        ) from exc
    if not isinstance(cfg, dict):
        raise AgentTrustAuditorConfigError("configuration root must be a table")
    section = cfg.get("agent_trust_auditor")
    if section is None:
        raise AgentTrustAuditorDisabled(
            "[agent_trust_auditor] enable = true is required"
        )
    if not isinstance(section, dict):
        raise AgentTrustAuditorConfigError(
            "agent_trust_auditor must be a table"
        )
    if section.get("enable") is not True:
        raise AgentTrustAuditorDisabled(
            "[agent_trust_auditor] enable = true is required"
        )
    stale = section.get("stale_after_seconds", _DEFAULT_STALE_AFTER_SECONDS)
    if isinstance(stale, bool) or not isinstance(stale, (int, float)):
        raise AgentTrustAuditorConfigError(
            "agent_trust_auditor.stale_after_seconds must be a number"
        )
    stale_seconds = float(stale)
    if not math.isfinite(stale_seconds) or not 60 <= stale_seconds <= 366 * 24 * 60 * 60:
        raise AgentTrustAuditorConfigError(
            "agent_trust_auditor.stale_after_seconds is out of range"
        )
    return {"stale_after_seconds": stale_seconds}


def enabled() -> bool:
    """Deployment-global feature gate; malformed policy fails closed."""
    try:
        _settings()
        return True
    except AgentTrustAuditorError:
        return False


def _require_enabled() -> dict[str, Any]:
    return _settings()


def _safe_projection_config() -> dict[str, Any]:
    try:
        from .config import config_source_errors, load_config

        cfg = load_config()
        errors = config_source_errors()
    except Exception as exc:
        raise AgentTrustAuditorConfigError(
            "active estate configuration is unavailable"
        ) from exc
    if errors:
        raise AgentTrustAuditorConfigError(
            "active estate configuration is unreadable"
        )
    if not isinstance(cfg, dict):
        raise AgentTrustAuditorConfigError("active configuration root must be a table")
    return cfg


def _snapshot(
    *,
    source: str,
    source_id: str,
    entity_type: str,
    display_name: str,
    status: str,
    observed_tools: object = None,
    posture: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    namespace = _source(source)
    source_record = _source_id(source_id)
    kind = _required_text(entity_type, "entity_type", 64)
    name = _required_text(display_name, "display_name", 256)
    state = _required_text(status, "status", 64)
    entity = stable_entity_id(namespace, source_record)
    return {
        "entity_id": entity,
        "identity": {
            "source": namespace,
            "source_id": source_record,
            "binding_sha256": _identity_digest(namespace, source_record),
        },
        "entity_type": kind,
        "display_name": name,
        "status": state,
        "observed_tools": _name_list(observed_tools, "observed tool"),
        "posture": dict(posture or {}),
        "metadata": dict(metadata or {}),
    }


def _graph_attributes(snapshot: dict[str, Any]) -> dict[str, Any]:
    identity = snapshot["identity"]
    posture = snapshot["posture"]
    attributes: dict[str, Any] = {
        "entity_id": snapshot["entity_id"],
        "entity_type": snapshot["entity_type"],
        "identity_binding_sha256": identity["binding_sha256"],
        "identity_source": identity["source"],
        "identity_source_id": identity["source_id"],
        "observed_tools": snapshot["observed_tools"],
        "snapshot_sha256": _sha256(snapshot),
    }
    for key in ("allow_all_tools", "pin_required", "pinned"):
        if key in posture:
            attributes[f"posture.{key}"] = posture[key]
    for key, value in sorted(snapshot["metadata"].items()):
        attributes[f"observed.{key}"] = value
    return attributes


def _ingest_graph_observation(
    snapshot: dict[str, Any],
    *,
    actor: str,
    observed_at: float,
    source_id: str | None = None,
    valid_until: float | None = None,
) -> dict[str, Any]:
    from . import evidence_graph

    snapshot_sha = _sha256(snapshot)
    graph_source_id = source_id or (
        f"{snapshot['entity_id']}:{snapshot_sha}:{observed_at.hex()}"
    )
    kwargs = {
        "source": "agent_trust_auditor",
        "source_id": graph_source_id,
        "evidence_type": "agent_estate_observation",
        "title": f"Observed {snapshot['entity_type']}: {snapshot['display_name']}",
        "summary": (
            "Bounded identity and posture metadata observed by the Agent Trust "
            "Auditor; content and credentials are not included."
        ),
        "controls": [],
        "attributes": _graph_attributes(snapshot),
        "links": [{
            "relation": "observes",
            "target_type": "agent_estate_entity",
            "target_id": snapshot["entity_id"],
        }],
        "actor": actor,
        "observed_at": observed_at,
        "valid_until": valid_until,
    }
    try:
        return evidence_graph.ingest(**kwargs)
    except RecordConflict:
        # A concurrent identical insert may win between EvidenceGraph's lookup
        # and create. Its second pass validates the winner's content binding.
        return evidence_graph.ingest(**kwargs)


def _assert_identity_binding(record: dict[str, Any], snapshot: dict[str, Any]) -> None:
    expected = snapshot["identity"]
    actual = record.get("identity")
    if (
        record.get("entity_id") != snapshot["entity_id"]
        or not isinstance(actual, dict)
        or actual.get("source") != expected["source"]
        or actual.get("source_id") != expected["source_id"]
        or actual.get("binding_sha256") != expected["binding_sha256"]
    ):
        raise IdentityCollisionError(
            "stable entity identity is already bound to another source record"
        )


def _observation_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = record.get("observations") or []
    if not isinstance(rows, list):
        raise AgentTrustAuditorStateError("observed asset has invalid citations")
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise AgentTrustAuditorStateError("observed asset has invalid citation")
        observation_id = _required_text(row.get("id"), "observation id", 64)
        observed_at = _timestamp(row.get("observed_at"))
        out.append({"id": observation_id, "observed_at": observed_at})
    return out


def _merge_observations(
    record: dict[str, Any],
    observation_id: str,
    observed_at: float,
) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in _observation_rows(record)}
    by_id[observation_id] = {"id": observation_id, "observed_at": observed_at}
    ordered = sorted(by_id.values(), key=lambda row: (row["observed_at"], row["id"]))
    return ordered[-_MAX_OBSERVATIONS_PER_ASSET:]


def _apply_observed_snapshot(
    record: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    observed_at: float,
    observation_id: str,
    replace_snapshot: bool,
) -> None:
    _assert_identity_binding(record, snapshot)
    current_last_seen = float(record.get("last_seen") or observed_at)
    snapshot_sha256 = _sha256(snapshot)
    # Evidence can arrive out of order.  Older observations remain useful as
    # citations and for first-seen history, but must never roll current posture,
    # metadata, or tool state backwards.  Equal-time conflicts are first-writer
    # wins; an identical equal-time snapshot may still add a new citation.
    if observed_at > current_last_seen:
        record["entity_type"] = snapshot["entity_type"]
        record["display_name"] = snapshot["display_name"]
        record["status"] = snapshot["status"]
        record["posture"] = dict(snapshot["posture"])
        if replace_snapshot:
            record["observed_tools"] = list(snapshot["observed_tools"])
            record["metadata"] = dict(snapshot["metadata"])
        else:
            record["observed_tools"] = sorted({
                *record.get("observed_tools", []),
                *snapshot["observed_tools"],
            })
            record["metadata"] = {
                **dict(record.get("metadata") or {}),
                **snapshot["metadata"],
            }
        record["snapshot_sha256"] = snapshot_sha256
    record["first_seen"] = min(float(record.get("first_seen") or observed_at), observed_at)
    record["last_seen"] = max(current_last_seen, observed_at)
    observations = _merge_observations(record, observation_id, observed_at)
    record["observations"] = observations
    record["observation_ids"] = [row["id"] for row in observations]


def _upsert_observed(
    snapshot: dict[str, Any],
    observation: dict[str, Any],
    *,
    actor: str,
    observed_at: float,
    replace_snapshot: bool,
) -> tuple[dict[str, Any], bool]:
    entity = snapshot["entity_id"]
    record_id = _record_id("ATO", entity)
    observation_id = _required_text(observation.get("id"), "observation id", 64)
    for _attempt in range(3):
        existing = _OBSERVED.get(record_id)
        if existing is None:
            record = {
                "id": record_id,
                "schema": _OBSERVED_SCHEMA,
                "entity_id": entity,
                "identity": dict(snapshot["identity"]),
                "entity_type": snapshot["entity_type"],
                "display_name": snapshot["display_name"],
                "status": snapshot["status"],
                "posture": dict(snapshot["posture"]),
                "observed_tools": list(snapshot["observed_tools"]),
                "metadata": dict(snapshot["metadata"]),
                "snapshot_sha256": _sha256(snapshot),
                "first_seen": observed_at,
                "last_seen": observed_at,
                "observations": [{"id": observation_id, "observed_at": observed_at}],
                "observation_ids": [observation_id],
            }
            try:
                return _OBSERVED.create(
                    record,
                    action="observe_asset",
                    actor=actor,
                ), False
            except RecordConflict:
                continue
        _assert_identity_binding(existing, snapshot)
        if observation_id in existing.get("observation_ids", []):
            return existing, True

        def _mutate(record: dict[str, Any]) -> None:
            _apply_observed_snapshot(
                record,
                snapshot,
                observed_at=observed_at,
                observation_id=observation_id,
                replace_snapshot=replace_snapshot,
            )

        try:
            updated = _OBSERVED.update(
                record_id,
                _mutate,
                expected_revision=int(existing.get("revision") or 0),
                action="observe_asset",
                actor=actor,
            )
        except RecordConflict:
            continue
        if updated is None:
            continue
        return updated, False
    raise RecordConflict("observed asset changed during bounded update retries")


def _record_observation(
    snapshot: dict[str, Any],
    *,
    actor: str,
    observed_at: float,
    replace_snapshot: bool,
    graph_source_id: str | None = None,
    valid_until: float | None = None,
) -> tuple[dict[str, Any], bool]:
    observation = _ingest_graph_observation(
        snapshot,
        actor=actor,
        observed_at=observed_at,
        source_id=graph_source_id,
        valid_until=valid_until,
    )
    return _upsert_observed(
        snapshot,
        observation,
        actor=actor,
        observed_at=observed_at,
        replace_snapshot=replace_snapshot,
    )


def _trust_snapshots(observed_at: float) -> list[dict[str, Any]]:
    from .agent_trust import load_trust_state

    enforced, registry = load_trust_state()
    out: list[dict[str, Any]] = []
    for agent_id, agent in sorted(registry.items()):
        active, lifecycle_reason = agent.is_active(observed_at)
        fingerprint = (
            hashlib.sha256(bytes.fromhex(agent.pubkey)).hexdigest()
            if agent.pubkey
            else ""
        )
        allow_tools = sorted(agent.allow_tools)
        out.append(_snapshot(
            source="agent_trust",
            source_id=agent_id,
            entity_type="agent",
            display_name=agent_id,
            status="active" if active else lifecycle_reason,
            observed_tools=allow_tools,
            posture={
                "allow_all_tools": not bool(allow_tools),
                "pin_required": True,
                "pinned": bool(agent.pubkey),
            },
            metadata={
                "budget_dollars_bounded": agent.max_dollars is not None,
                "budget_wall_bounded": agent.max_wall_seconds is not None,
                "data_scope_count": len(agent.data_scopes),
                "deny_tools": sorted(agent.deny_tools),
                "direction": agent.direction,
                "identity_fingerprint_sha256": fingerprint,
                "lifecycle_reason": lifecycle_reason,
                "max_risk": agent.max_risk,
                "trust_enforced": enforced,
            },
        ))
    return out


def _mcp_auth_mode(spec: Any) -> str:
    if spec.oauth:
        return "oauth"
    if spec.auth_token:
        return "bearer"
    if spec.headers:
        return "headers"
    return "none"


def _mcp_snapshots() -> list[dict[str, Any]]:
    from .mcp_client import load_mcp_specs_from_config

    snapshots: list[dict[str, Any]] = []
    for spec in sorted(load_mcp_specs_from_config(), key=lambda item: item.name):
        if spec.is_http:
            host = urlsplit(str(spec.url or "")).hostname or ""
            command = ""
            pin_required = False
            pinned: bool | None = None
            transport = "http"
        else:
            host = ""
            command = Path(spec.command).name
            pin_required = True
            pinned = bool(spec.pin_sha256)
            transport = "stdio"
        snapshots.append(_snapshot(
            source="mcp_config",
            source_id=spec.name,
            entity_type="mcp_server",
            display_name=spec.name,
            status="configured",
            posture={
                "allow_all_tools": False,
                "pin_required": pin_required,
                "pinned": pinned,
            },
            metadata={
                "argument_count": len(spec.args),
                "auth_mode": _mcp_auth_mode(spec),
                "command_name": command,
                "environment_entry_count": len(spec.env),
                "host": host,
                "inherit_environment": bool(spec.inherit_env),
                "transport": transport,
            },
        ))
    return snapshots


def _tool_names(tool_registry: Any | None) -> tuple[set[str], set[str]]:
    if tool_registry is None:
        return set(), set()
    try:
        registered_rows = list(islice(
            iter(tool_registry.all()),
            _MAX_TOOL_INVENTORY + 1,
        ))
        exposed_rows = list(islice(
            iter(tool_registry.exposed()),
            _MAX_TOOL_INVENTORY + 1,
        ))
        if (
            len(registered_rows) > _MAX_TOOL_INVENTORY
            or len(exposed_rows) > _MAX_TOOL_INVENTORY
        ):
            raise ValueError("tool inventory exceeds the bounded projection limit")
        registered = {
            _required_text(row.name, "registered tool name", 256)
            for row in registered_rows
        }
        exposed = {
            _required_text(row.name, "exposed tool name", 256)
            for row in exposed_rows
        }
    except (AttributeError, TypeError, ValueError) as exc:
        raise AgentTrustAuditorConfigError(
            "tool_registry must expose bounded all() and exposed() tool lists"
        ) from exc
    if not exposed <= registered:
        raise AgentTrustAuditorConfigError(
            "tool_registry exposes tools that are not registered"
        )
    return registered, exposed


def _tool_snapshots(tool_registry: Any | None = None) -> list[dict[str, Any]]:
    from .safety.tool_risk import risk_map
    from .tools import CORE_TOOL_NAMES

    registered, exposed = _tool_names(tool_registry)
    catalogued = set(CORE_TOOL_NAMES)
    names = catalogued | registered
    risks = risk_map(names)
    return [
        _snapshot(
            source="lightwork_tool",
            source_id=name,
            entity_type="tool",
            display_name=name,
            status=(
                "available" if name in exposed
                else "configured" if name in registered
                else "catalogued"
            ),
            posture={
                "allow_all_tools": False,
                "pin_required": False,
                "pinned": None,
            },
            metadata={
                "available": name in exposed,
                "catalogued": name in catalogued,
                "configured": name in registered,
                "risk": risks[name],
            },
        )
        for name in sorted(names)
    ]


def _provider_snapshots(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    providers = cfg.get("providers") or {}
    if not isinstance(providers, dict):
        raise AgentTrustAuditorConfigError("providers must be a table")
    snapshots: list[dict[str, Any]] = []
    for provider, provider_cfg in sorted(providers.items()):
        if not isinstance(provider_cfg, dict):
            continue
        name = _required_text(provider, "provider name", 128)
        snapshots.append(_snapshot(
            source="provider_config",
            source_id=name,
            entity_type="provider",
            display_name=name,
            status="configured",
            posture={
                "allow_all_tools": False,
                "pin_required": False,
                "pinned": None,
            },
            metadata={"configured": True},
        ))
    return snapshots


def refresh_local_projection(
    *,
    actor: str,
    observed_at: float | None = None,
    tool_registry: Any | None = None,
) -> dict[str, Any]:
    """Project configured estate metadata without changing its authority.

    Configured MCP specs are loaded through the runtime MCP config loader.  The
    installable registry/catalog is intentionally not consulted: availability
    in a catalog is not deployment evidence.
    """
    settings = _require_enabled()
    stamp = _not_future(
        _timestamp(observed_at),
        time.time(),
        label="observed_at",
    )
    cfg = _safe_projection_config()
    snapshots = [
        *_trust_snapshots(stamp),
        *_mcp_snapshots(),
        *_tool_snapshots(tool_registry),
        *_provider_snapshots(cfg),
    ]
    records: list[dict[str, Any]] = []
    deduplicated = 0
    for snapshot in sorted(snapshots, key=lambda row: row["entity_id"]):
        record, duplicate = _record_observation(
            snapshot,
            actor=actor,
            observed_at=stamp,
            replace_snapshot=True,
            valid_until=stamp + settings["stale_after_seconds"],
        )
        records.append(record)
        deduplicated += int(duplicate)
    counts: dict[str, int] = {}
    for snapshot in snapshots:
        entity_type = snapshot["entity_type"]
        counts[entity_type] = counts.get(entity_type, 0) + 1
    return {
        "observed_at": stamp,
        "records": sorted(records, key=lambda row: row["entity_id"]),
        "counts": {key: counts[key] for key in sorted(counts)},
        "deduplicated": deduplicated,
    }


def _decode_otel_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if not isinstance(value, dict):
        return _MISSING
    for key in ("stringValue", "string_value"):
        if key in value and isinstance(value[key], str):
            return value[key]
    for key in ("intValue", "int_value", "doubleValue", "double_value"):
        if key in value:
            raw = value[key]
            if isinstance(raw, bool):
                return _MISSING
            try:
                number = float(raw)
            except (TypeError, ValueError):
                return _MISSING
            if not math.isfinite(number):
                return _MISSING
            return int(number) if number.is_integer() else number
    for key in ("boolValue", "bool_value"):
        if key in value and isinstance(value[key], bool):
            return value[key]
    return _MISSING


def _otel_attributes(value: object) -> tuple[dict[str, object], int]:
    if value is None:
        return {}, 0
    pairs: list[tuple[object, object]] = []
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and "key" in item:
                pairs.append((item.get("key"), item.get("value")))
            else:
                pairs.append((None, None))
    else:
        raise ValueError("OTel attributes must be an object or key/value list")
    if len(pairs) > _MAX_OTEL_ATTRIBUTES:
        raise ValueError("OTel span exceeds the attribute limit")
    try:
        from .safety.secret_detector import scan as scan_secrets
    except Exception:  # pragma: no cover - defensive import boundary
        scan_secrets = None
    safe: dict[str, object] = {}
    dropped = 0
    for raw_key, raw_value in pairs:
        if not isinstance(raw_key, str):
            dropped += 1
            continue
        key = raw_key.strip()
        if key not in _OTEL_ALLOWED or _CONTENT_KEY_RE.search(key):
            dropped += 1
            continue
        decoded = _decode_otel_value(raw_value)
        if decoded is _MISSING:
            dropped += 1
            continue
        if isinstance(decoded, str):
            decoded = decoded.strip()
            if not decoded or _CONTROL_RE.search(decoded):
                dropped += 1
                continue
            try:
                contains_secret = scan_secrets is None or bool(scan_secrets(decoded))
            except Exception:  # detector failure is a fail-closed drop
                contains_secret = True
            if contains_secret:
                dropped += 1
                continue
            decoded = decoded[:256]
        safe[key] = decoded
    return {key: safe[key] for key in sorted(safe)}, dropped


def _resource_spans(payload: dict[str, Any]) -> list[tuple[dict[str, Any], object]]:
    rows: list[tuple[dict[str, Any], object]] = []
    resource_spans = payload.get("resourceSpans", payload.get("resource_spans", []))
    if not isinstance(resource_spans, list):
        raise ValueError("OTLP resourceSpans must be a list")
    for resource_group in resource_spans:
        if not isinstance(resource_group, dict):
            continue
        resource = resource_group.get("resource") or {}
        resource_attributes = (
            resource.get("attributes", []) if isinstance(resource, dict) else []
        )
        scope_spans = resource_group.get(
            "scopeSpans",
            resource_group.get("scope_spans", []),
        )
        if not isinstance(scope_spans, list):
            continue
        for scope_group in scope_spans:
            if not isinstance(scope_group, dict):
                continue
            spans = scope_group.get("spans") or []
            if not isinstance(spans, list):
                continue
            rows.extend(
                (span, resource_attributes)
                for span in spans
                if isinstance(span, dict)
            )
    return rows


def _normalised_spans(payload: object) -> list[tuple[dict[str, Any], object]]:
    if isinstance(payload, list):
        rows = [(span, {}) for span in payload if isinstance(span, dict)]
    elif isinstance(payload, dict) and (
        "resourceSpans" in payload or "resource_spans" in payload
    ):
        rows = _resource_spans(payload)
    elif isinstance(payload, dict) and isinstance(payload.get("spans"), list):
        rows = [
            (span, payload.get("resource_attributes") or {})
            for span in payload["spans"]
            if isinstance(span, dict)
        ]
    elif isinstance(payload, dict):
        rows = [(payload, {})]
    else:
        raise ValueError("OTel payload must be a span list or OTLP object")
    if len(rows) > _MAX_OTEL_SPANS:
        raise ValueError("OTel payload exceeds the span limit")
    return rows


def _span_ids(span: dict[str, Any]) -> tuple[str, str]:
    trace_id = str(span.get("trace_id", span.get("traceId", ""))).strip().lower()
    span_id = str(span.get("span_id", span.get("spanId", ""))).strip().lower()
    if not _TRACE_RE.fullmatch(trace_id):
        raise ValueError("OTel trace_id must be 32 lowercase hexadecimal characters")
    if not _SPAN_RE.fullmatch(span_id):
        raise ValueError("OTel span_id must be 16 lowercase hexadecimal characters")
    return trace_id, span_id


def _span_timestamp(span: dict[str, Any], fallback: float) -> float:
    nanos = span.get("start_time_unix_nano", span.get("startTimeUnixNano"))
    if nanos is not None:
        try:
            stamp = float(nanos) / 1_000_000_000
        except (TypeError, ValueError) as exc:
            raise ValueError("OTel start time is invalid") from exc
        observed = _timestamp(stamp)
        return _not_future(observed, fallback, label="OTel start time")
    seconds = span.get("timestamp")
    observed = fallback if seconds is None else _timestamp(float(seconds))
    return _not_future(observed, fallback, label="OTel timestamp")


def _otel_identity(attributes: dict[str, object]) -> tuple[str, str] | None:
    raw_id = (
        attributes.get("gen_ai.agent.id")
        or attributes.get("gen_ai.agent.name")
        or attributes.get("service.name")
    )
    if not isinstance(raw_id, str):
        return None
    try:
        identity = _source_id(raw_id)
    except ValueError:
        return None
    display = attributes.get("gen_ai.agent.name") or identity
    if not isinstance(display, str):
        display = identity
    return identity, display


def _otel_snapshot(
    source: str,
    attributes: dict[str, object],
) -> dict[str, Any] | None:
    identity = _otel_identity(attributes)
    if identity is None:
        return None
    source_id, display_name = identity
    tool = attributes.get("gen_ai.tool.name")
    observed_tools = [tool] if isinstance(tool, str) and tool else []
    metadata = {
        key: value
        for key, value in attributes.items()
        if key not in {
            "gen_ai.agent.id",
            "gen_ai.agent.name",
            "gen_ai.tool.name",
        }
    }
    return _snapshot(
        source=f"otel.{source}",
        source_id=source_id,
        entity_type="agent",
        display_name=display_name,
        status="observed",
        observed_tools=observed_tools,
        posture={
            "allow_all_tools": False,
            "pin_required": False,
            "pinned": None,
        },
        metadata=metadata,
    )


def ingest_otel_spans(
    payload: object,
    *,
    source: str,
    actor: str,
    received_at: float | None = None,
) -> dict[str, Any]:
    """Ingest bounded OTel GenAI identity/tool metadata, never content."""
    _require_enabled()
    collector = _source(source)
    if len(collector) > 59:
        raise ValueError("OTel source exceeds 59 characters")
    received = _not_future(
        _timestamp(received_at),
        time.time(),
        label="received_at",
    )
    records: dict[str, dict[str, Any]] = {}
    observation_ids: list[str] = []
    accepted = 0
    deduplicated = 0
    ignored = 0
    dropped_attributes = 0
    for span, resource_attributes in _normalised_spans(payload):
        trace_id, span_id = _span_ids(span)
        resource_safe, resource_dropped = _otel_attributes(resource_attributes)
        span_safe, span_dropped = _otel_attributes(span.get("attributes"))
        attributes = {**resource_safe, **span_safe}
        dropped = resource_dropped + span_dropped
        dropped_attributes += dropped
        snapshot = _otel_snapshot(collector, attributes)
        if snapshot is None:
            ignored += 1
            continue
        observed = _span_timestamp(span, received)
        record, duplicate = _record_observation(
            snapshot,
            actor=actor,
            observed_at=observed,
            replace_snapshot=False,
            graph_source_id=f"otel.{collector}:{trace_id}:{span_id}",
        )
        records[record["entity_id"]] = record
        observation_ids.extend(record["observation_ids"])
        deduplicated += int(duplicate)
        accepted += int(not duplicate)
    return {
        "accepted": accepted,
        "deduplicated": deduplicated,
        "ignored": ignored,
        "dropped_attributes": dropped_attributes,
        "observation_ids": sorted(set(observation_ids)),
        "records": [records[key] for key in sorted(records)],
    }


def _declaration_id(entity_id: str) -> str:
    return _record_id("ATD", entity_id)


def _lifecycle(value: object) -> str:
    lifecycle = _required_text(value, "lifecycle", 32).lower()
    if lifecycle not in _LIFECYCLES:
        raise ValueError(f"lifecycle must be one of {sorted(_LIFECYCLES)}")
    return lifecycle


def _risk_tier(value: object) -> str:
    risk = _required_text(value, "risk_tier", 32).lower()
    if risk not in _RISK_TIERS:
        raise ValueError(f"risk_tier must be one of {sorted(_RISK_TIERS)}")
    return risk


def declare_entity(
    entity_id: str,
    *,
    owner: str = "",
    purpose: str = "",
    risk_tier: str = "medium",
    lifecycle: str = "proposed",
    allowed_tools: object = None,
    actor: str,
) -> dict[str, Any]:
    """Create human declaration metadata; later changes require explicit CAS."""
    _require_enabled()
    entity = _entity_id(entity_id)
    record_id = _declaration_id(entity)
    existing = _DECLARED.get(record_id)
    if existing is not None:
        if existing.get("entity_id") != entity:
            raise IdentityCollisionError(
                "declaration identity is already bound to another entity"
            )
        raise RecordConflict("entity already has a declaration")
    record = {
        "id": record_id,
        "schema": _DECLARED_SCHEMA,
        "entity_id": entity,
        "owner": _optional_text(owner, "owner", 256),
        "purpose": _optional_text(purpose, "purpose", 2000),
        "risk_tier": _risk_tier(risk_tier),
        "lifecycle": _lifecycle(lifecycle),
        "allowed_tools": _name_list(allowed_tools, "allowed tool"),
        "status": _lifecycle(lifecycle),
    }
    return _DECLARED.create(record, action="declare_entity", actor=actor)


def update_declaration(
    entity_id: str,
    *,
    expected_revision: int,
    actor: str,
    owner: object = _MISSING,
    purpose: object = _MISSING,
    risk_tier: object = _MISSING,
    lifecycle: object = _MISSING,
    allowed_tools: object = _MISSING,
) -> dict[str, Any] | None:
    """CAS-update human metadata and enforce lifecycle transitions."""
    _require_enabled()
    entity = _entity_id(entity_id)
    if all(
        value is _MISSING
        for value in (owner, purpose, risk_tier, lifecycle, allowed_tools)
    ):
        raise ValueError("at least one declaration field must be updated")

    def _mutate(record: dict[str, Any]) -> None:
        if record.get("entity_id") != entity:
            raise IdentityCollisionError(
                "declaration identity is already bound to another entity"
            )
        if owner is not _MISSING:
            record["owner"] = _optional_text(owner, "owner", 256)
        if purpose is not _MISSING:
            record["purpose"] = _optional_text(purpose, "purpose", 2000)
        if risk_tier is not _MISSING:
            record["risk_tier"] = _risk_tier(risk_tier)
        if allowed_tools is not _MISSING:
            record["allowed_tools"] = _name_list(
                allowed_tools,
                "allowed tool",
            )
        if lifecycle is not _MISSING:
            current = _lifecycle(record.get("lifecycle"))
            target = _lifecycle(lifecycle)
            if target != current and target not in _LIFECYCLE_TRANSITIONS[current]:
                raise ValueError(
                    f"lifecycle transition {current!r} -> {target!r} is not allowed"
                )
            record["lifecycle"] = target
            record["status"] = target

    return _DECLARED.update(
        _declaration_id(entity),
        _mutate,
        expected_revision=expected_revision,
        action="update_declaration",
        actor=actor,
    )


def get_declaration(entity_id: str) -> dict[str, Any] | None:
    entity = _entity_id(entity_id)
    record = _DECLARED.get(_declaration_id(entity))
    if record is not None and record.get("entity_id") != entity:
        raise IdentityCollisionError(
            "declaration identity is already bound to another entity"
        )
    return record


def list_declarations() -> list[dict[str, Any]]:
    return sorted(_DECLARED.list(), key=lambda row: str(row.get("entity_id") or ""))


def get_observed(entity_id: str) -> dict[str, Any] | None:
    entity = _entity_id(entity_id)
    record = _OBSERVED.get(_record_id("ATO", entity))
    if record is not None and record.get("entity_id") != entity:
        raise IdentityCollisionError(
            "observed identity is already bound to another entity"
        )
    return record


def list_observed_estate() -> list[dict[str, Any]]:
    return sorted(_OBSERVED.list(), key=lambda row: str(row.get("entity_id") or ""))


def _finding(
    kind: str,
    entity: dict[str, Any],
    *,
    severity: str,
    summary: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    identity = {
        "finding_type": kind,
        "entity_id": entity["entity_id"],
    }
    observation_ids = sorted(set(entity.get("observation_ids") or []))
    if not observation_ids:
        raise AgentTrustAuditorStateError(
            "finding cannot be emitted without an observation citation"
        )
    return {
        "id": f"ATF-{_sha256(identity)[:24]}",
        "finding_type": kind,
        "severity": severity,
        "entity_id": entity["entity_id"],
        "entity_type": entity["entity_type"],
        "display_name": entity["display_name"],
        "summary": summary,
        "details": details,
        "observation_ids": observation_ids,
        "citations": [
            {"observation_id": observation_id, "relation": "supports"}
            for observation_id in observation_ids
        ],
    }


def _active_policy_declaration(
    declaration: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return only a declaration whose governed lifecycle confers policy."""

    if not isinstance(declaration, dict):
        return None
    lifecycle = str(declaration.get("lifecycle") or "").strip().lower()
    status = str(declaration.get("status") or "").strip().lower()
    if lifecycle not in _ACTIVE_POLICY_LIFECYCLES or status != lifecycle:
        return None
    return declaration


def _entity_findings(
    entity: dict[str, Any],
    declaration: dict[str, Any] | None,
    *,
    now: float,
    stale_after_seconds: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kind = str(entity.get("entity_type") or "")
    active_declaration = _active_policy_declaration(declaration)
    if kind in _OWNABLE_TYPES and not str(
        (active_declaration or {}).get("owner") or ""
    ).strip():
        out.append(_finding(
            "unowned",
            entity,
            severity="medium",
            summary="The observed asset has no active declared accountable owner.",
            details={
                "declaration_id": (declaration or {}).get("id") or "",
                "declaration_lifecycle": (
                    str((declaration or {}).get("lifecycle") or "")
                ),
                "policy_active": active_declaration is not None,
            },
        ))
    posture = entity.get("posture") or {}
    if posture.get("pin_required") is True and posture.get("pinned") is not True:
        out.append(_finding(
            "unpinned",
            entity,
            severity="high",
            summary="The external asset has no source identity pin.",
            details={"identity_source": (entity.get("identity") or {}).get("source")},
        ))
    if kind == "agent" and posture.get("allow_all_tools") is True:
        out.append(_finding(
            "overprivileged",
            entity,
            severity="high",
            summary="The observed agent has an allow-all tool ceiling.",
            details={"reason": "allow_all_tools"},
        ))
    if kind in _OWNABLE_TYPES:
        last_seen = float(entity.get("last_seen") or 0)
        age = max(0.0, now - last_seen)
        if age > stale_after_seconds:
            out.append(_finding(
                "stale",
                entity,
                severity="medium",
                summary="The asset has not been observed inside the freshness window.",
                details={
                    "age_seconds": int(age),
                    "last_seen": last_seen,
                    "stale_after_seconds": stale_after_seconds,
                },
            ))
    declared_tools = set((active_declaration or {}).get("allowed_tools") or [])
    undeclared_tools = sorted(set(entity.get("observed_tools") or []) - declared_tools)
    if undeclared_tools:
        out.append(_finding(
            "undeclared-tool",
            entity,
            severity="high",
            summary="The asset used or exposes tools absent from its declaration.",
            details={
                "declared_tools": sorted(declared_tools),
                "undeclared_tools": undeclared_tools,
                "declaration_lifecycle": (
                    str((declaration or {}).get("lifecycle") or "")
                ),
                "policy_active": active_declaration is not None,
            },
        ))
    return out


def findings(*, now: float | None = None) -> list[dict[str, Any]]:
    """Return deterministic findings bound to exact evidence observation IDs."""
    settings = _require_enabled()
    current = _timestamp(now)
    declarations = {row["entity_id"]: row for row in list_declarations()}
    out: list[dict[str, Any]] = []
    for entity in list_observed_estate():
        out.extend(_entity_findings(
            entity,
            declarations.get(entity["entity_id"]),
            now=current,
            stale_after_seconds=settings["stale_after_seconds"],
        ))
    return sorted(
        out,
        key=lambda row: (row["entity_id"], row["finding_type"], row["id"]),
    )


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "_audit_pending"}


def _pack_body(*, generated_at: float, actor: str) -> dict[str, Any]:
    from . import evidence_graph
    from .paths import current_tenant_id

    observed = [_public_record(row) for row in list_observed_estate()]
    declared = [_public_record(row) for row in list_declarations()]
    report = findings(now=generated_at)
    observation_ids = sorted({
        observation_id
        for record in observed
        for observation_id in record.get("observation_ids", [])
    })
    nodes: list[dict[str, Any]] = []
    for observation_id in observation_ids:
        node = evidence_graph.get(observation_id)
        if node is None:
            raise AgentTrustAuditorStateError(
                f"evidence graph observation {observation_id!r} is missing"
            )
        nodes.append(_public_record(node))
    graph_bound = {
        "nodes": sorted(nodes, key=lambda row: row["id"]),
        "observation_ids": observation_ids,
    }
    return {
        "schema": _PACK_SCHEMA,
        "generated_at": generated_at,
        "generated_by": _required_text(actor, "actor", 256),
        "tenant": current_tenant_id() or "",
        "observed_estate": observed,
        "declared_estate": declared,
        "findings": report,
        "evidence_graph": {
            **graph_bound,
            "graph_sha256": _sha256(graph_bound),
        },
        "note": (
            "This inventory and finding pack is cryptographically bound evidence, "
            "not a certification or legal determination."
        ),
    }


def _attestation_path() -> Path:
    from .paths import data_dir

    return data_dir("agent_trust_auditor", "evidence_pack_attestations.ndjson")


def _find_attestation(path: Path, attestation_id: str) -> dict[str, Any]:
    # The just-appended row is necessarily in the bounded tail. A unique nonce
    # avoids returning another process's concurrent pack receipt.
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 1024 * 1024))
        tail = handle.read()
    for raw_line in reversed(tail.splitlines()):
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if row.get("attestation_id") == attestation_id:
            return row
    raise AgentTrustAuditorStateError("signed pack attestation could not be recovered")


def _sign_pack(digest: str, *, actor: str, generated_at: float) -> dict[str, Any]:
    from .audit import signing

    path = _attestation_path()
    signer = signing.AuditSigner(path)
    attestation_id = uuid.uuid4().hex
    event = {
        "v": 1,
        "event": _PACK_EVENT,
        "attestation_id": attestation_id,
        "actor": _required_text(actor, "actor", 256),
        "generated_at": generated_at,
        "pack_sha256": digest,
    }
    if not signer.write(event):
        raise AgentTrustAuditorStateError("audit signer refused the evidence pack")
    receipt = _find_attestation(path, attestation_id)
    return {
        "algorithm": "Ed25519",
        "key_id": receipt["key_id"],
        "public_key": signer.public_key_hex,
        "signature": receipt["sig"],
        "event_hash": receipt["hash"],
        "signed_message": "hex-decoded event_hash",
        "receipt": receipt,
    }


def render_evidence_pack(
    *,
    actor: str,
    generated_at: float | None = None,
) -> dict[str, Any]:
    """Render and Ed25519-sign a pack over declarations, findings, and nodes."""
    _require_enabled()
    stamp = _timestamp(generated_at)
    wall_clock = time.time()
    if abs(stamp - wall_clock) > _MAX_FUTURE_SKEW_SECONDS:
        raise ValueError("generated_at exceeds the clock-skew allowance")
    body = _pack_body(generated_at=stamp, actor=actor)
    digest = _sha256(body)
    return {
        **body,
        "pack_sha256": digest,
        "attestation": _sign_pack(digest, actor=actor, generated_at=stamp),
    }


def verify_evidence_pack(
    pack: dict[str, Any],
    *,
    trusted_pubkeys: Mapping[str, str] | None = None,
) -> bool:
    """Verify a pack only against an externally supplied key-id registry.

    The public key disclosed inside the pack is informational and must match the
    trusted registry entry.  It never establishes trust by itself.
    """
    try:
        from .audit.signing import verify_ed25519

        if not isinstance(trusted_pubkeys, Mapping) or not trusted_pubkeys:
            return False
        body = dict(pack)
        digest = body.pop("pack_sha256")
        attestation = body.pop("attestation")
        if not isinstance(digest, str) or _sha256(body) != digest:
            return False
        if not isinstance(attestation, dict):
            return False
        if (
            attestation.get("algorithm") != "Ed25519"
            or attestation.get("signed_message") != "hex-decoded event_hash"
        ):
            return False
        receipt = attestation.get("receipt")
        public_key = attestation.get("public_key")
        if not isinstance(receipt, dict) or not isinstance(public_key, str):
            return False
        if (
            receipt.get("event") != _PACK_EVENT
            or receipt.get("pack_sha256") != digest
            or receipt.get("sig") != attestation.get("signature")
            or receipt.get("hash") != attestation.get("event_hash")
        ):
            return False
        key_id = receipt.get("key_id")
        if not isinstance(key_id, str):
            return False
        trusted_public_key = trusted_pubkeys.get(key_id)
        if not isinstance(trusted_public_key, str):
            return False
        trusted_public_key = trusted_public_key.strip().lower()
        trusted_key_bytes = bytes.fromhex(trusted_public_key)
        if len(trusted_key_bytes) != 32:
            return False
        expected_key_id = hashlib.sha256(trusted_key_bytes).hexdigest()[:16]
        if key_id != expected_key_id:
            return False
        if not hmac.compare_digest(public_key.strip().lower(), trusted_public_key):
            return False
        if receipt.get("key_id") != key_id or attestation.get("key_id") != key_id:
            return False
        unsigned = {
            key: value
            for key, value in receipt.items()
            if key not in {"hash", "sig"}
        }
        row_hash = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if row_hash != receipt.get("hash"):
            return False
        return verify_ed25519(
            trusted_public_key,
            receipt["sig"],
            bytes.fromhex(row_hash),
        )
    except (KeyError, TypeError, ValueError):
        return False


__all__ = [
    "AgentTrustAuditorConfigError",
    "AgentTrustAuditorDisabled",
    "AgentTrustAuditorError",
    "AgentTrustAuditorStateError",
    "IdentityCollisionError",
    "declare_entity",
    "enabled",
    "findings",
    "get_declaration",
    "get_observed",
    "ingest_otel_spans",
    "list_declarations",
    "list_observed_estate",
    "refresh_local_projection",
    "render_evidence_pack",
    "stable_entity_id",
    "update_declaration",
    "verify_evidence_pack",
]
