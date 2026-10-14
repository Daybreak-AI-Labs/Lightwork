"""Replica-safety inventory and fail-closed authority readiness.

Lightwork has several persistence mechanisms.  A Postgres world model does not
make file-backed flow publication, identity policy, idempotency claims, or
promotion state agree across replicas.  This module keeps that distinction
machine-readable:

* every known mutable store belongs to one consistency family;
* every family is classified as authority, evidence, immutable, derived, or
  cache, and as node-local or shared;
* enterprise and multi-replica admission fail closed while an authority is
  local, an expected store is unregistered, or a required shared service has
  not been positively probed.

The default report is intentionally conservative.  Configuration proves only
that an operator *declared* Postgres, Redis, and an audit object store.  It is
not a network check.  Callers that own service clients can inject bounded probes
and the report records that distinction explicitly.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import multiprocessing
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

STORE_CLASSES = frozenset({
    "authority",
    "evidence",
    "immutable",
    "derived",
    "cache",
})
STORE_BACKENDS = frozenset({"local", "shared"})
REQUIRED_SHARED_SERVICES = ("postgres", "redis", "audit_object_store")
REPORT_SCHEMA = "maverick.authority-readiness/v1"
REPLICA_COUNT_ENV = "MAVERICK_REPLICA_COUNT"
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_PROBE_RESPONSE_BYTES = 64 * 1024
_HOLDOUT_AUTHORITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HOLDOUT_AUTHORITY_ISSUE_PREFIX = "shared holdout authority is not ready: "


@dataclass(frozen=True)
class StoreSpec:
    """One transactional consistency family in the mutable-state inventory."""

    name: str
    classification: str
    backend: str
    service: str | None = None
    source: str = ""
    detail: str = ""
    content_digest: str | None = None
    readiness_issue: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("store name must not be empty")
        if self.classification not in STORE_CLASSES:
            raise ValueError(
                f"unsupported store classification {self.classification!r}"
            )
        if self.backend not in STORE_BACKENDS:
            raise ValueError(f"unsupported store backend {self.backend!r}")
        if self.backend == "local" and self.service is not None:
            raise ValueError("a local store cannot name a shared service")
        if (
            self.content_digest is not None
            and not _SHA256_DIGEST.fullmatch(self.content_digest)
        ):
            raise ValueError("content digest must be canonical sha256:<hex>")


@dataclass(frozen=True)
class InactiveStoreSpec:
    """Evidence that a known consistency family cannot currently mutate."""

    name: str
    source: str
    evidence: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("inactive store name must not be empty")
        if not self.source.strip():
            raise ValueError("inactive store source must not be empty")
        if not self.evidence.strip():
            raise ValueError("inactive store evidence must not be empty")


@dataclass(frozen=True)
class ServiceDeclaration:
    """Sanitized declaration status; no credential-bearing values are retained."""

    name: str
    declared: bool
    detail: str


Probe = Callable[[], bool | tuple[bool, str] | Mapping[str, Any]]


class AuthorityStateNotReady(RuntimeError):
    """Raised when strict admission is required and readiness is red."""

    def __init__(self, report: dict[str, Any]):
        blockers = ", ".join(item["code"] for item in report["blockers"])
        super().__init__(f"authority state is not ready: {blockers or 'unknown'}")
        self.report = report


class AuthorityStateRegistry:
    """Explicit registry of discovered mutable-store consistency families.

    ``expect``/``discover`` is the fail-closed hook for a newly found store.  It
    remains unregistered until code assigns an allowed classification/backend.
    That makes omissions visible instead of silently treating them as local
    cache data.
    """

    def __init__(self, *, expected_stores: Iterable[str] = ()) -> None:
        self._expected: set[str] = set()
        self._stores: dict[str, StoreSpec] = {}
        self._inactive: dict[str, InactiveStoreSpec] = {}
        for name in expected_stores:
            self.expect(name)

    @classmethod
    def from_specs(cls, specs: Iterable[StoreSpec]) -> AuthorityStateRegistry:
        registry = cls()
        for spec in specs:
            registry.register(spec)
        return registry

    def expect(self, name: str) -> None:
        normalized = str(name).strip()
        if not normalized:
            raise ValueError("expected store name must not be empty")
        self._expected.add(normalized)

    discover = expect

    def register(self, spec: StoreSpec) -> None:
        if spec.name in self._stores:
            raise ValueError(f"store {spec.name!r} is already registered")
        if spec.name in self._inactive:
            raise ValueError(f"store {spec.name!r} is already marked inactive")
        self._expected.add(spec.name)
        self._stores[spec.name] = spec

    def mark_inactive(self, spec: InactiveStoreSpec) -> None:
        """Record a non-active family without masking discovered state.

        A caller-provided ``expected_stores`` entry is an independent discovery
        signal.  Refusing to mark it inactive prevents feature heuristics from
        suppressing a store that another scanner has positively observed.
        """
        if spec.name in self._expected or spec.name in self._stores:
            raise ValueError(
                f"store {spec.name!r} was discovered and cannot be marked inactive"
            )
        if spec.name in self._inactive:
            raise ValueError(f"store {spec.name!r} is already marked inactive")
        self._inactive[spec.name] = spec

    @property
    def expected_stores(self) -> tuple[str, ...]:
        return tuple(sorted(self._expected))

    @property
    def stores(self) -> tuple[StoreSpec, ...]:
        return tuple(self._stores[name] for name in sorted(self._stores))

    @property
    def inactive_stores(self) -> tuple[InactiveStoreSpec, ...]:
        return tuple(self._inactive[name] for name in sorted(self._inactive))

    @property
    def unregistered_stores(self) -> tuple[str, ...]:
        return tuple(sorted(self._expected.difference(self._stores)))

    def readiness_report(
        self,
        *,
        enterprise: bool,
        replica_count: int | None,
        enforcement_reasons: Iterable[str],
        services: Mapping[str, Mapping[str, Any]],
        config_errors: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Return a JSON-safe admission and HA-eligibility report."""
        reasons = tuple(dict.fromkeys(str(item) for item in enforcement_reasons))
        errors = tuple(sorted(str(item) for item in config_errors))
        enforcement_required = bool(reasons)
        unregistered = self.unregistered_stores
        local_authorities = tuple(
            spec.name
            for spec in self.stores
            if spec.classification == "authority" and spec.backend == "local"
        )
        unbound_shared_authorities = tuple(
            spec.name
            for spec in self.stores
            if (
                spec.classification == "authority"
                and spec.backend == "shared"
                and not spec.service
            )
        )
        holdout_authority_issues = tuple(
            spec.detail.removeprefix(_HOLDOUT_AUTHORITY_ISSUE_PREFIX)
            for spec in self.stores
            if (
                spec.name == "holdout_query_budget"
                and spec.detail.startswith(_HOLDOUT_AUTHORITY_ISSUE_PREFIX)
            )
        )
        deployment_input_issues = tuple(
            spec.name
            for spec in self.stores
            if spec.readiness_issue
        )

        service_rows: dict[str, dict[str, Any]] = {}
        unavailable_services: list[str] = []
        for name in REQUIRED_SHARED_SERVICES:
            raw = services.get(name) or {}
            row = {
                "declared": raw.get("declared") is True,
                "probed": raw.get("probed") is True,
                "available": raw.get("available") is True,
                "detail": str(raw.get("detail") or "status unavailable"),
            }
            service_rows[name] = row
            if not (row["declared"] and row["probed"] and row["available"]):
                unavailable_services.append(name)

        unknown_services = sorted({
            str(spec.service)
            for spec in self.stores
            if spec.backend == "shared"
            and spec.service
            and spec.service not in service_rows
        })
        blockers: list[dict[str, Any]] = []
        if errors:
            blockers.append({
                "code": "config_unreadable",
                "stores": [],
                "detail": "deployment configuration could not be trusted",
            })
        if replica_count is None:
            blockers.append({
                "code": "replica_count_invalid",
                "stores": [],
                "detail": "replica count is absent from the trusted parse result",
            })
        if unregistered:
            blockers.append({
                "code": "unregistered_stores",
                "stores": list(unregistered),
                "detail": "discovered stores have no authority classification",
            })
        if local_authorities:
            blockers.append({
                "code": "local_authority_stores",
                "stores": list(local_authorities),
                "detail": "node-local authority can diverge across replicas",
            })
        if unbound_shared_authorities:
            blockers.append({
                "code": "unbound_shared_authorities",
                "stores": list(unbound_shared_authorities),
                "detail": "shared authority is not bound to a service declaration",
            })
        if holdout_authority_issues:
            blockers.append({
                "code": "holdout_authority_id_invalid",
                "stores": ["holdout_query_budget"],
                "detail": holdout_authority_issues[0],
            })
        if deployment_input_issues:
            blockers.append({
                "code": "deployment_inputs_unverified",
                "stores": list(deployment_input_issues),
                "detail": (
                    "external or immutable deployment inputs did not match "
                    "their configured content-digest proof"
                ),
            })
        if unavailable_services:
            blockers.append({
                "code": "shared_services_unavailable",
                "stores": unavailable_services,
                "detail": "required services are undeclared, unprobed, or unavailable",
            })
        if unknown_services:
            blockers.append({
                "code": "unknown_shared_services",
                "stores": unknown_services,
                "detail": "store registry names unsupported shared services",
            })

        ha_eligible = not blockers
        # Community/single-replica admission is unchanged.  Its report remains
        # honest about HA eligibility, but the compatibility path is not blocked.
        ready = ha_eligible if enforcement_required else True
        counts = dict.fromkeys(sorted(STORE_CLASSES), 0)
        for spec in self.stores:
            counts[spec.classification] += 1
        store_rows = []
        for spec in self.stores:
            row = asdict(spec)
            row["blocks_ha"] = (
                (
                    spec.classification == "authority"
                    and spec.backend == "local"
                )
                or bool(spec.readiness_issue)
            )
            store_rows.append(row)

        return {
            "schema": REPORT_SCHEMA,
            "ready": ready,
            "admission_ready": ready,
            "ha_eligible": ha_eligible,
            "enforcement_required": enforcement_required,
            "enforcement_reasons": list(reasons),
            "enterprise": bool(enterprise),
            "replica_count": replica_count,
            "config_healthy": not errors,
            "config_errors": list(errors),
            "counts": counts,
            "expected_store_count": len(self._expected),
            "registered_store_count": len(self._stores),
            "inactive_store_count": len(self._inactive),
            "inventory_store_count": len(self._stores) + len(self._inactive),
            "unregistered_stores": list(unregistered),
            "local_authorities": list(local_authorities),
            "inactive_stores": [asdict(spec) for spec in self.inactive_stores],
            "services": service_rows,
            "blockers": blockers,
            "stores": store_rows,
        }


def _store(
    name: str,
    classification: str,
    source: str,
    detail: str,
) -> StoreSpec:
    return StoreSpec(
        name=name,
        classification=classification,
        backend="local",
        source=source,
        detail=detail,
    )


# The unit of registration is a transactional consistency family, not each
# shard/day/tenant file.  For example all per-tenant usage ledgers are one store
# family because they share an implementation and migration boundary.
_LOCAL_STORE_SPECS = (
    # Admission, identity, policy, idempotency, and current-state authorities.
    _store("world_model", "authority", "world_model.py", "goals, jobs, approvals, facts"),
    _store("flow_control_plane", "authority", "flow/store.py", "drafts, publication pointers, releases, run idempotency"),
    _store("a2a_task_claims", "authority", "a2a_tasks.py", "durable message-id claims"),
    _store("automation_job_queue", "authority", "maverick_dashboard/automation_queue.py", "scheduler jobs and ownership"),
    _store("network_job_queue", "authority", "queue_dispatcher.py", "distributed dispatch and replay ownership"),
    _store("job_dispatch_claims", "authority", "queue_dispatcher.py; world_model.py", "fleet-wide at-most-once dispatch claims"),
    _store("tenant_registry", "authority", "tenant/registry.py", "tenant lifecycle, plan, and quota"),
    _store("tenant_policy_overlays", "authority", "tenant/kms.py; config.py", "tenant-scoped policy"),
    _store("tenant_wrapped_deks", "authority", "tenant/security_authority.py; tenant/kms.py", "KMS-wrapped per-tenant data keys"),
    _store("tenant_usage_ledgers", "authority", "billing.py", "billable usage and limits"),
    _store("tenant_spend_reservations", "authority", "tenant/registry.py", "atomic quota reservations"),
    _store("dashboard_settings", "authority", "maverick_dashboard/settings_store.py", "runtime provider/channel policy"),
    _store("runtime_overrides", "authority", "runtime_overrides.py", "budget, model, and tool policy overrides"),
    _store("role_overrides", "authority", "role_edit.py", "role-to-model policy"),
    _store("suite_grants", "authority", "suite_grants.py", "department authorization grants"),
    _store("access_policy", "authority", "access_policy.py", "principal access decisions"),
    _store("capability_revocations", "authority", "revocation.py", "capability deny state"),
    _store("agent_trust_registry", "authority", "agent_trust.py", "trusted agent identities and constraints"),
    _store("internal_agent_bus_delivery", "authority", "agent_bus.py", "cross-replica inbox delivery and acknowledgement"),
    _store("handoff_nonce_claims", "authority", "handoff.py; bus_handoff.py", "single-use signed handoff nonces"),
    _store("handoff_issuer_state", "authority", "bus_handoff.py", "run-scoped handoff issuer keys and rotation"),
    _store("dashboard_users_roles", "authority", "maverick_dashboard/rbac.py", "dashboard principals and roles"),
    _store("dashboard_tenant_roles", "authority", "maverick_dashboard/rbac.py", "tenant role assignments"),
    _store("dashboard_invitations", "authority", "maverick_dashboard/invites.py", "invitation lifecycle"),
    _store("session_revocations", "authority", "maverick_dashboard/session_revocation.py", "session revocation epochs"),
    _store("saml_replay_claims", "authority", "maverick_dashboard/saml_replay.py", "consumed assertion ids"),
    _store("oidc_transaction_claims", "authority", "maverick_dashboard/oidc_login.py", "single-use login transactions and shared-outage posture"),
    _store("issue_webhook_replay_claims", "authority", "maverick_dashboard/app.py", "single-use signed issue-webhook deliveries"),
    _store("oidc_subject_directory", "authority", "maverick_dashboard/subject_directory.py", "subject bindings and tombstones"),
    _store("scim_directory", "authority", "maverick_dashboard/scim.py", "provisioned users and groups"),
    _store("oauth_vaults", "authority", "oauth_vault.py", "connector authorization credentials"),
    _store("browser_auth_vaults", "authority", "browser_auth_vault.py", "browser authorization state"),
    _store("kill_switch", "authority", "agent.py; maverick_dashboard/api.py", "global execution halt"),
    _store("event_trigger_registry", "authority", "maverick_dashboard/event_triggers_store.py", "scheduled event definitions and cursors"),
    _store("saas_trigger_registry", "authority", "maverick_dashboard/triggers_store.py", "webhook and SaaS triggers"),
    _store("configuration_proposals", "authority", "tools/quorum_approval.py", "quorum-approved config changes"),
    _store("mcp_registry_overlay", "authority", "runtime_overrides.py; mcp_registry.py", "dashboard-managed MCP servers"),
    _store("supply_chain_pins", "authority", "supply_chain.py", "trusted artifact identities"),
    _store("channel_federation_delivery", "authority", "channel_federation.py", "outbox and replay claims"),
    _store("federation_ingress_claims", "authority", "federation.py", "signed ingress replay, rate, and correlation claims"),
    _store("marketplace_federation_state", "authority", "marketplace/federation.py", "listing import watermarks and withdrawal state"),
    _store("insight_exchange_replay_claims", "authority", "insight_exchange.py", "single-use signed learning bundles"),
    _store("fleet_agent_roster", "authority", "fleet_memory.py", "agent ownership and fleet-memory registration"),
    _store("tool_token_replay_claims", "authority", "tool_token.py", "single-use delegated tool tokens"),
    _store("work_discovery_enrollment", "authority", "work_discovery_identity.py; work_discovery_store.py", "edge enrollment, leases, and revocation epochs"),
    _store(
        "deployment_encryption_key",
        "immutable",
        "crypto_at_rest.py",
        "external fleet encryption-key identity for shared encrypted authorities",
    ),
    _store("approval_signing_keys", "authority", "approval_signing.py", "trusted human approver identities and key rotation"),
    _store("audit_signing_keys", "authority", "audit/signing.py", "fleet audit signer identity and key rotation"),
    _store("task_graphs", "authority", "task_graph.py", "durable orchestration state"),
    _store("self_improvement_promotion", "authority", "self_improvement.py", "promotion and rollback ledger"),
    _store("self_learning_state", "authority", "self_learning.py; learning_store.py", "governed learning admission state"),
    _store("self_harness_learning_state", "authority", "learning_store.py; self_harness.py; self_harness_eval.py", "harness addenda, line metadata, transfer memory, and evaluation corpus"),
    _store("adapter_activation", "authority", "adapter_rung.py; artifact_release_authority.py", "cluster desired release and node activation observations"),
    _store("verifier_artifact_activation", "authority", "verifier_head.py; prm.py", "active verifier digest, rollback pointer, and serving epoch"),
    _store("verifier_confirmation_claims", "authority", "verifier_head.py", "single-use verifier confirmation cohorts"),
    _store("evaluator_slot_epochs", "authority", "evaluator_evolution.py", "active evaluator identity, epoch, and erasure tombstones"),
    _store("calibration_freeze_state", "authority", "calibration.py; self_improvement_runner.py", "fleet learning freeze verdicts and risk receipts"),
    _store("holdout_query_budget", "authority", "self_harness_holdout.py", "global sealed-holdout query and alpha spend"),
    _store("factory_correction_activation", "authority", "factory_learning.py", "active correction artifact and rollback pointer"),
    _store("learning_rollout_authority", "authority", "learning_rollout.py; dreaming.py", "live learned-behavior generation and rollback"),
    _store("budget_tuning_policy", "authority", "self_tuning_budget.py", "learned runtime budget ceilings"),
    _store("dgm_pack_activation", "authority", "maverick_evolve/adopt.py", "operator-adopted domain-pack release pointer"),
    _store("effect_execution_receipts", "authority", "effect_protocol.py", "external-effect idempotency and outcome"),
    _store("security_governance_records", "authority", "governed_records.py", "security control lifecycle"),
    _store("privacy_governance_records", "authority", "governed_records.py", "privacy control lifecycle"),
    _store("cra_product_security_records", "authority", "cra_ops.py; governed_records.py", "CRA product, release, case, filing, and VEX lifecycle"),
    _store("model_risk_assurance_records", "authority", "model_risk_assurance.py; governed_records.py", "AI-system inventory, evaluation, change, third-party risk, and promotion decisions"),
    _store("production_assurance_campaigns", "authority", "evidence_factory.py; governed_records.py", "assurance campaign admission and finalization"),
    _store("agent_trust_declarations", "authority", "agent_trust_auditor.py; governed_records.py", "declared agent identities and expected authority"),
    _store("form_submissions", "authority", "form_store.py", "submitted operational records"),
    _store("public_form_tenant_routing", "authority", "form_store.py; maverick_dashboard/app.py:/form/{token}", "tenant-by-user anonymous token-to-tenant routing; route currently fails closed"),
    _store("hunter_response_coordination", "authority", "platform_hunt/postgres_store.py; env_hunt/store.py", "response-effect claims, audit outbox, and scheduler leases"),
    _store("audit_append_authority", "authority", "audit/writer.py; audit/signing.py", "canonical event idempotency, sequencing, and signer fencing"),
    # Historical evidence and append-only observations.
    _store("audit_log_chain", "evidence", "audit/writer.py; audit/signing.py", "signed audit day-files and anchors"),
    _store("audit_worm_manifest", "evidence", "audit/worm.py", "object-lock export locators"),
    _store("budget_receipts", "evidence", "budget_receipts.py", "signed spend receipts"),
    _store("assessment_register", "evidence", "assessments.py", "assessment results and status history"),
    _store("flow_evolution_log", "evidence", "flow/evolution_log.py", "flow improvement history"),
    _store("flow_node_outcomes", "evidence", "flow/node_outcomes.py", "node outcome observations"),
    _store("goal_feedback", "evidence", "maverick_dashboard/goal_feedback_store.py", "operator outcome feedback"),
    _store("trigger_event_log", "evidence", "maverick_dashboard/trigger_events_store.py", "trigger delivery observations"),
    _store("platform_hunt_records", "evidence", "platform_hunt/store.py", "platform findings and evidence"),
    _store("environment_hunt_records", "evidence", "env_hunt/store.py", "environment findings and evidence"),
    _store("evidence_graph", "evidence", "evidence_graph.py", "evidence lineage and campaign observations"),
    _store("production_assurance_drills", "evidence", "evidence_factory.py; governed_records.py", "crash, restore, partition, and upgrade drill records"),
    _store("agent_trust_observations", "evidence", "agent_trust_auditor.py; governed_records.py", "observed agent identities, grants, and traffic"),
    _store("agent_trust_attestations", "evidence", "agent_trust_auditor.py", "trust-audit attestations"),
    _store("trajectory_outbox", "evidence", "trajectory_store.py", "learning trajectories"),
    _store("user_notes", "evidence", "user_notes.py", "operator-authored durable notes"),
    _store("attachments_and_captures", "evidence", "attachments.py; tools/computer.py", "captured source evidence"),
    # Mutable collections whose published members are content-addressed/immutable.
    _store("flow_release_objects", "immutable", "flow/store.py", "content-addressed flow release objects"),
    _store("assessment_template_releases", "immutable", "assessment.py", "content-addressed assessment templates"),
    _store("adapter_artifacts", "immutable", "adapter_rung.py", "signed adapter artifacts and manifests"),
    _store("workspace_snapshots", "immutable", "workspace_snapshot.py", "content-bound workspace snapshots"),
    _store("backup_archives", "immutable", "backup.py", "hashed backup manifests and payloads"),
    # Recomputable projections.
    _store("vector_indexes", "derived", "vector_store/", "rebuildable semantic indexes"),
    _store("benchmark_history", "derived", "benchmark_retrospective.py", "benchmark projections and reports"),
    _store("role_statistics", "derived", "role_stats.py", "usage aggregates"),
    _store("reliability_certificates", "derived", "reliability_cert.py", "derived reliability reports"),
    _store("ux_preferences", "derived", "ux_store.py", "non-authoritative user-interface state"),
    _store("achievements", "derived", "achievements.py", "recomputable product achievements"),
    # Explicitly disposable acceleration state.
    _store("llm_response_cache", "cache", "cache/llm.py", "LLM response cache"),
    _store("tool_result_cache", "cache", "cache/tool.py", "tool result cache"),
    _store("learning_cache", "cache", "cache/learning.py", "learning lookup cache"),
    _store("skill_embedding_cache", "cache", "cache/__init__.py", "skill embedding cache"),
    _store("catalog_cache", "cache", "catalog.py", "remote catalog cache"),
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _shared_service_url(value: str, *, schemes: frozenset[str]) -> bool:
    """Require a network DSN that cannot resolve literally to this node.

    A loopback declaration is useful for development but is not evidence of a
    replica-shared authority service. DNS names are still live-probed before
    admission; this check only removes the obvious false-positive forms.
    """
    try:
        parsed = urlsplit(value)
        host = str(parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if parsed.scheme.lower() not in schemes or not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_loopback or address.is_unspecified)


def _postgres_declaration(config: Mapping[str, Any]) -> ServiceDeclaration:
    world = _mapping(config.get("world_model"))
    world_backend = (
        _text(os.environ.get("MAVERICK_WORLD_BACKEND"))
        or _text(world.get("backend"))
    ).lower()
    governed = _mapping(config.get("governed_records"))
    governed_backend = (
        _text(os.environ.get("MAVERICK_GOVERNED_RECORDS_BACKEND"))
        or _text(governed.get("backend"))
        or "auto"
    ).lower()
    dsn = (
        _text(os.environ.get("MAVERICK_PG_DSN"))
        or _text(world.get("dsn"))
    )
    valid_dsn = _shared_service_url(
        dsn, schemes=frozenset({"postgres", "postgresql"})
    )
    backend_declared = world_backend == "postgres" or governed_backend == "postgres"
    declared = backend_declared and valid_dsn
    missing = []
    if not backend_declared:
        missing.append("a Postgres authority backend")
    if not valid_dsn:
        missing.append("valid Postgres DSN")
    detail = (
        "Postgres backend and credential-bearing DSN are declared; not live-verified"
        if declared
        else "missing " + ", ".join(missing)
    )
    return ServiceDeclaration("postgres", declared, detail)


def _redis_declaration(config: Mapping[str, Any]) -> ServiceDeclaration:
    queue = _mapping(config.get("queue"))
    backend = _text(queue.get("backend")).lower()
    dsn = (
        _text(os.environ.get("MAVERICK_QUEUE_REDIS_DSN"))
        or _text(queue.get("redis_dsn"))
    )
    namespace = (
        _text(os.environ.get("MAVERICK_QUEUE_NAMESPACE"))
        or _text(queue.get("namespace"))
    )
    signing_key = (
        _text(os.environ.get("MAVERICK_QUEUE_SIGNING_KEY"))
        or _text(queue.get("signing_key"))
    )
    valid_transport = False
    try:
        valid_transport = _shared_service_url(
            dsn, schemes=frozenset({"rediss"})
        )
    except ValueError:
        pass
    valid_namespace = bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", namespace))
    valid_signing_key = len(signing_key.encode("utf-8")) >= 32
    declared = (
        backend == "arq"
        and valid_transport
        and valid_namespace
        and valid_signing_key
    )
    missing = []
    if backend != "arq":
        missing.append("queue.backend=arq")
    if not valid_transport:
        missing.append("non-local rediss DSN")
    if not valid_namespace:
        missing.append("valid queue namespace")
    if not valid_signing_key:
        missing.append("queue signing key of at least 32 UTF-8 bytes")
    detail = (
        "Redis queue, TLS transport, namespace, and signing key are declared; "
        "not live-verified"
        if declared
        else "missing " + ", ".join(missing)
    )
    return ServiceDeclaration("redis", declared, detail)


def _audit_object_store_declaration(
    config: Mapping[str, Any],
) -> ServiceDeclaration:
    audit = _mapping(config.get("audit"))
    worm = _mapping(audit.get("worm"))
    provider = _text(worm.get("provider")).lower()
    bucket = _text(worm.get("bucket"))
    mode = (_text(worm.get("mode")) or "COMPLIANCE").upper()
    worm_env = os.environ.get("MAVERICK_AUDIT_WORM")
    env_allows = worm_env is None or worm_env.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    declared = env_allows and provider in {"s3", "aws"} and bool(bucket) and mode in {
        "COMPLIANCE",
        "GOVERNANCE",
    }
    missing = []
    if provider not in {"s3", "aws"}:
        missing.append("audit.worm provider=s3")
    if not bucket:
        missing.append("object-lock bucket")
    if mode not in {"COMPLIANCE", "GOVERNANCE"}:
        missing.append("valid object-lock mode")
    if not env_allows:
        missing.append("audit WORM not disabled by environment")
    detail = (
        "S3 object-lock target, bucket, and retention mode are declared; "
        "not live-verified"
        if declared
        else "missing " + ", ".join(missing)
    )
    return ServiceDeclaration("audit_object_store", declared, detail)


def service_declarations(
    config: Mapping[str, Any],
) -> dict[str, ServiceDeclaration]:
    """Return sanitized configuration declarations for required HA services."""
    return {
        item.name: item
        for item in (
            _postgres_declaration(config),
            _redis_declaration(config),
            _audit_object_store_declaration(config),
        )
    }


def _probe_process_entry(connection, payload: bytes) -> None:
    try:
        import cloudpickle

        probe = cloudpickle.loads(payload)
        try:
            response = ("result", probe())
        except BaseException as exc:  # noqa: BLE001 - only type crosses boundary
            response = ("error", type(exc).__name__)
        serialized = cloudpickle.dumps(response)
        if len(serialized) > _MAX_PROBE_RESPONSE_BYTES:
            serialized = cloudpickle.dumps(("error", "ProbeResponseTooLarge"))
        connection.send_bytes(serialized)
    except BaseException:  # noqa: BLE001 - parent treats missing frame as failure
        pass
    finally:
        connection.close()


def _run_probe(
    probe: Probe,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Run a probe in a killable child and retain no caller-controlled detail."""
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        return False, "live probe timeout is invalid"
    if not 0.01 <= timeout <= 60.0:
        return False, "live probe timeout is invalid"
    try:
        import cloudpickle

        payload = cloudpickle.dumps(probe)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:  # noqa: BLE001 - serialization fails closed
        return False, f"live probe could not start ({type(exc).__name__})"
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_probe_process_entry,
        args=(sender, payload),
        name="authority-readiness-probe",
        daemon=True,
    )
    try:
        process.start()
    except (KeyboardInterrupt, SystemExit):
        receiver.close()
        sender.close()
        raise
    except BaseException as exc:  # noqa: BLE001 - process startup fails closed
        receiver.close()
        sender.close()
        return False, f"live probe could not start ({type(exc).__name__})"
    sender.close()
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(1.0)
        if process.is_alive():  # pragma: no cover - terminate normally suffices
            process.kill()
            process.join(1.0)
        receiver.close()
        return False, "live probe timed out"
    try:
        if not receiver.poll():
            return False, "live probe exited without a result"
        response = cloudpickle.loads(
            receiver.recv_bytes(_MAX_PROBE_RESPONSE_BYTES)
        )
    except BaseException as exc:  # noqa: BLE001 - malformed frame fails closed
        return False, f"live probe result was unreadable ({type(exc).__name__})"
    finally:
        receiver.close()
    if (
        not isinstance(response, tuple)
        or len(response) != 2
        or response[0] not in {"result", "error"}
    ):
        return False, "live probe returned an invalid frame"
    if response[0] == "error":
        error_type = str(response[1] or "Error")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error_type):
            error_type = "Error"
        return False, f"live probe raised {error_type}"
    result = response[1]
    if isinstance(result, bool):
        return result, "live probe passed" if result else "live probe failed"
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
        return result[0], "live probe passed" if result[0] else "live probe failed"
    if isinstance(result, Mapping) and isinstance(result.get("available"), bool):
        available = bool(result["available"])
        return available, "live probe passed" if available else "live probe failed"
    return False, "live probe returned an invalid result"


def probe_services(
    declarations: Mapping[str, ServiceDeclaration],
    probes: Mapping[str, Probe] | None = None,
    *,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Combine declarations with optional positive live-probe evidence."""
    probes = probes or {}
    rows: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_SHARED_SERVICES:
        declaration = declarations[name]
        row: dict[str, Any] = {
            "declared": declaration.declared,
            "probed": False,
            "available": False,
            "detail": declaration.detail,
        }
        probe = probes.get(name)
        if declaration.declared and probe is not None:
            available, probe_detail = _run_probe(
                probe, timeout_seconds=timeout_seconds
            )
            row.update({
                "probed": True,
                "available": available,
                "detail": probe_detail,
            })
        elif declaration.declared:
            row["detail"] = declaration.detail + "; live probe not supplied"
        rows[name] = row
    return rows


def _runtime_selected_text(
    env_name: str,
    configured: object,
    *,
    default: object = "",
) -> str:
    """Mirror runtime ``env or config or default`` selection exactly.

    A whitespace-only environment value is truthy before normalization, so it
    deliberately shadows configuration and then becomes empty. Readiness must
    not silently fall back to a healthy configured value when runtime will
    reject or select a different backend.
    """
    environment = os.environ.get(env_name)
    selected = environment or configured or default
    return str(selected).strip()


def _postgres_world_declared(config: Mapping[str, Any]) -> bool:
    world = _mapping(config.get("world_model"))
    backend = _runtime_selected_text(
        "MAVERICK_WORLD_BACKEND",
        world.get("backend"),
        default="sqlite",
    ).lower()
    dsn = _runtime_selected_text("MAVERICK_PG_DSN", world.get("dsn"))
    return backend == "postgres" and _shared_service_url(
        dsn, schemes=frozenset({"postgres", "postgresql"})
    )


def _governed_records_postgres_declared(config: Mapping[str, Any]) -> bool:
    """Whether governed records, specifically, select a shared Postgres DSN."""
    section = _mapping(config.get("governed_records"))
    configured = _runtime_selected_text(
        "MAVERICK_GOVERNED_RECORDS_BACKEND",
        section.get("backend"),
        default="auto",
    ).lower()
    if configured == "local" or configured not in {"auto", "postgres"}:
        return False
    if configured == "auto":
        return _postgres_world_declared(config)
    world = _mapping(config.get("world_model"))
    dsn = _runtime_selected_text("MAVERICK_PG_DSN", world.get("dsn"))
    return _shared_service_url(
        dsn, schemes=frozenset({"postgres", "postgresql"})
    )


def _self_harness_postgres_declared(config: Mapping[str, Any]) -> bool:
    """Whether the deployment-global self-harness selects shared Postgres."""
    harness = _mapping(config.get("self_harness"))
    store = _text(harness.get("store")).lower()
    return store == "world" and _postgres_world_declared(config)


def _holdout_authority_id_issue(config: Mapping[str, Any]) -> str | None:
    """Return a sanitized readiness issue for the shared study identity."""
    harness = _mapping(config.get("self_harness"))
    if "holdout_authority_id" not in harness:
        return (
            "self_harness.holdout_authority_id is missing from the declared "
            "shared deployment"
        )
    value = harness.get("holdout_authority_id")
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _HOLDOUT_AUTHORITY_ID.fullmatch(value)
    ):
        return (
            "self_harness.holdout_authority_id is invalid in the declared "
            "shared deployment"
        )
    return None


def _redis_queue_declared(config: Mapping[str, Any]) -> bool:
    return _redis_declaration(config).declared


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _feature_bool(
    config: Mapping[str, Any],
    *,
    section: str,
    key: str,
    env_name: str,
    default: bool,
) -> bool | None:
    """Resolve a feature flag without treating malformed input as disabled.

    ``None`` means the registry could not prove either posture.  Callers keep
    the corresponding local authority active in that case, so a typo cannot
    manufacture a green HA report.
    """
    if env_name in os.environ and os.environ.get(env_name, "").strip():
        raw_env = os.environ[env_name].strip().lower()
        if raw_env in _TRUE_VALUES:
            return True
        if raw_env in _FALSE_VALUES:
            return False
        return None
    if section not in config:
        return default
    raw_section = config.get(section)
    if not isinstance(raw_section, Mapping):
        return None
    if key not in raw_section:
        return default
    value = raw_section.get(key)
    return value if isinstance(value, bool) else None


def _oidc_browser_login_declared(config: Mapping[str, Any]) -> bool:
    """Whether the built-in OIDC browser flow is provably configured.

    This mirrors :func:`maverick.oidc.login_enabled` from the already-loaded
    deployment-global configuration snapshot.  It deliberately does not call
    the tenant-aware config loader: an ambient request tenant must not waive a
    deployment-wide invite-session signing requirement.  Malformed values are
    treated as absent, which can only make readiness more conservative.
    """
    auth = _mapping(config.get("auth"))
    oidc = _mapping(auth.get("oidc"))
    enabled_env = os.environ.get("MAVERICK_OIDC_ENABLED")
    if enabled_env is not None:
        enabled = enabled_env.strip().lower() in _TRUE_VALUES
    else:
        enabled = oidc.get("enabled") is True
    if not enabled:
        return False

    client_id = (
        _text(os.environ.get("MAVERICK_OIDC_CLIENT_ID"))
        or _text(oidc.get("client_id"))
    )
    session_secret = (
        _text(os.environ.get("MAVERICK_OIDC_SESSION_SECRET"))
        or _text(oidc.get("session_secret"))
    )
    issuer = (
        _text(os.environ.get("MAVERICK_OIDC_ISSUER"))
        or _text(oidc.get("issuer"))
    )
    authorization_endpoint = (
        _text(os.environ.get("MAVERICK_OIDC_AUTHORIZATION_ENDPOINT"))
        or _text(oidc.get("authorization_endpoint"))
    )
    token_endpoint = (
        _text(os.environ.get("MAVERICK_OIDC_TOKEN_ENDPOINT"))
        or _text(oidc.get("token_endpoint"))
    )
    return bool(
        client_id
        and session_secret
        and (issuer or (authorization_endpoint and token_endpoint))
    )


def _invite_session_signing_proof(
    config: Mapping[str, Any],
) -> tuple[str | None, str]:
    """Bind and report the non-secret digest for shared local invite sessions.

    Invitation acceptance mints a local dashboard session only when invitations
    may be enabled and the built-in OIDC browser-login flow is not configured.
    In that posture, every replica must receive the same external HMAC key.  A
    successful CAS bind proves the key matches the deployment-global identity
    authority; only its SHA-256 digest is retained in the readiness inventory.
    """
    invites = _feature_bool(
        config,
        section="dashboard",
        key="invites",
        env_name="MAVERICK_DASHBOARD_INVITES",
        default=False,
    )
    if invites is False or _oidc_browser_login_declared(config):
        return None, ""

    dashboard = _mapping(config.get("dashboard"))
    secret = (
        _text(os.environ.get("MAVERICK_DASHBOARD_SESSION_SECRET"))
        or _text(dashboard.get("session_secret"))
    )
    if not secret:
        return None, (
            "shared local invite sessions require a configured dashboard "
            "session-signing key"
        )
    try:
        secret_bytes = secret.encode("utf-8")
    except UnicodeEncodeError:
        return None, "configured dashboard session-signing key is invalid"
    if not 32 <= len(secret_bytes) <= 4096:
        return None, "configured dashboard session-signing key is invalid"

    digest = hashlib.sha256(secret_bytes).hexdigest()
    try:
        from .identity_authority import bind_session_signing_key

        bound = bind_session_signing_key(secret)
    except Exception:
        return None, (
            "dashboard session-signing digest could not be bound to the "
            "shared invitation authority"
        )
    if not isinstance(bound, str) or not hmac.compare_digest(bound, digest):
        return None, (
            "dashboard session-signing digest could not be bound to the "
            "shared invitation authority"
        )
    return f"sha256:{digest}", ""


def _identity_federation_proof(
    config: Mapping[str, Any],
) -> tuple[str | None, str | None, str]:
    """Bind the complete shared identity-policy and SCIM-secret contract.

    The four effective-authorization families are promoted as one unit. The
    binding reads deployment-global configuration, refuses every legacy JSON
    authority, and CAS-binds both the canonical policy digest and the aggregate
    digest of the externally held SCIM bearer set. No bearer secret is returned
    or retained in the inventory.
    """
    try:
        from .identity_federation import bind_deployment_contract

        policy_digest, secret_digest = bind_deployment_contract(config)
    except Exception:
        return None, None, (
            "shared identity policy and SCIM-secret proofs could not be bound"
        )
    if not (
        isinstance(policy_digest, str)
        and _SHA256_DIGEST.fullmatch(policy_digest)
        and isinstance(secret_digest, str)
        and _SHA256_DIGEST.fullmatch(secret_digest)
    ):
        return None, None, (
            "shared identity policy and SCIM-secret proofs are invalid"
        )
    return policy_digest, secret_digest, ""


_IDENTITY_FEDERATION_STORES = frozenset({
    "dashboard_users_roles",
    "oidc_subject_directory",
    "scim_directory",
    "suite_grants",
})

_GOVERNED_STORE_DETAIL_SUFFIXES = {
    "budget_tuning_policy": (
        "; tenant-scoped deterministic governed CAS with stable observation ids"
    ),
    "self_learning_state": (
        "; bounded tenant CAS admissions with stable observation ids and backend time"
    ),
    "verifier_confirmation_claims": (
        "; tenant one-shot cohort claims with backend time and same-intent recovery"
    ),
    "verifier_artifact_activation": (
        "; promotion-chain-bound exact desired digest, predecessor CAS, and "
        "per-node healthy observation"
    ),
    "factory_correction_activation": (
        "; promotion-chain-bound exact desired digest, predecessor CAS, and "
        "per-node healthy observation"
    ),
    "dgm_pack_activation": (
        "; archive- and promotion-chain-bound exact desired digest, predecessor "
        "CAS, and per-node healthy observation"
    ),
    "event_trigger_registry": (
        "; tenant-scoped governed CAS with backend-clock cursor epochs and "
        "active-tenant scheduler enumeration"
    ),
    "saas_trigger_registry": (
        "; tenant-scoped governed CAS with ambiguity-safe anonymous routing"
    ),
    "public_form_tenant_routing": (
        "; deployment-global digest-only route lifecycle with epoch-bound "
        "submission claims and reconciliation"
    ),
}


def _deployment_encryption_key_spec(
    config: Mapping[str, Any],
    original: StoreSpec,
    *,
    governed_postgres: bool,
) -> StoreSpec | InactiveStoreSpec:
    """Bind shared encrypted records to one externally pinned fleet key.

    A single-node/local governed-record deployment does not have a shared
    decryptability boundary, so the input is explicitly inactive there.  Once
    governed Postgres is selected, HA eligibility requires the full external
    digest proof even if the current process is still admitted through the
    single-replica compatibility path.
    """
    if not governed_postgres:
        return _inactive(
            original,
            "no shared encrypted governed-record authority is selected",
        )
    digest: str | None = None
    issue = ""
    try:
        from .crypto_at_rest import (
            deployment_at_rest_enabled,
            require_shared_deployment_key_identity,
        )

        if not deployment_at_rest_enabled(config=config, source_errors=()):
            issue = (
                "deployment-global application at-rest encryption is disabled "
                "or unverified"
            )
        else:
            digest = require_shared_deployment_key_identity()
    except Exception:
        issue = (
            "external fleet encryption key and canonical digest could not be "
            "verified"
        )
    if digest is not None and not _SHA256_DIGEST.fullmatch(digest):
        digest = None
        issue = "external fleet encryption-key digest proof is invalid"
    return StoreSpec(
        name=original.name,
        classification="immutable",
        backend="local",
        source=original.source,
        detail=(
            "external fleet encryption key; exact deployment digest verified"
            if not issue
            else "external fleet encryption-key identity is not deployment-verified"
        ),
        content_digest=digest,
        readiness_issue=issue,
    )


def _identity_federation_inputs(
    config: Mapping[str, Any],
    governed_postgres: bool,
) -> tuple[str | None, str | None, str]:
    if not governed_postgres:
        return None, None, ""
    return _identity_federation_proof(config)


def _identity_federation_store_spec(
    original: StoreSpec,
    *,
    governed_postgres: bool,
    policy_digest: str | None,
    secret_digest: str | None,
    readiness_issue: str,
) -> StoreSpec:
    if not governed_postgres:
        return original
    if readiness_issue:
        return StoreSpec(
            name=original.name,
            classification=original.classification,
            backend="local",
            source=original.source,
            detail=(
                original.detail + "; shared identity contract is not fully proven"
            ),
            readiness_issue=readiness_issue,
        )
    content_digest = (
        secret_digest if original.name == "scim_directory" else policy_digest
    )
    return StoreSpec(
        name=original.name,
        classification=original.classification,
        backend="shared",
        service="postgres",
        source=original.source,
        detail=(
            original.detail
            + "; encrypted deployment-global identity CAS; canonical policy "
            "and external SCIM bearer-set digests fleet-bound"
        ),
        content_digest=content_digest,
    )


_WORLD_SHARED_STORES = frozenset({
    "world_model",
    "automation_job_queue",
    "job_dispatch_claims",
    "network_job_queue",
    "a2a_task_claims",
    "effect_execution_receipts",
    "flow_control_plane",
    "flow_release_objects",
    "kill_switch",
    "oidc_transaction_claims",
    "issue_webhook_replay_claims",
    "self_improvement_promotion",
    "platform_hunt_records",
    "environment_hunt_records",
    "hunter_response_coordination",
    "audit_append_authority",
})


def _world_shared_store_spec(
    original: StoreSpec,
    *,
    postgres: bool,
    redis: bool,
) -> StoreSpec:
    is_queue = original.name == "network_job_queue"
    shared = redis if is_queue else postgres
    if not shared:
        return original
    return StoreSpec(
        name=original.name,
        classification=original.classification,
        backend="shared",
        service="redis" if is_queue else "postgres",
        source=original.source,
        detail=original.detail,
    )


def _self_harness_store_spec(
    original: StoreSpec,
    *,
    harness_postgres: bool,
) -> StoreSpec:
    if not harness_postgres:
        return original
    return StoreSpec(
        name=original.name,
        classification=original.classification,
        backend="shared",
        service="postgres",
        source=original.source,
        detail=original.detail,
    )


def _inactive(original: StoreSpec, evidence: str) -> InactiveStoreSpec:
    return InactiveStoreSpec(
        name=original.name,
        source=original.source,
        evidence=evidence,
    )


def _approval_input_spec(
    config: Mapping[str, Any], original: StoreSpec,
) -> StoreSpec | InactiveStoreSpec:
    """Describe the external approver trust set without retaining public keys."""
    raw_section = config.get("self_improvement", {})
    if not isinstance(raw_section, Mapping):
        return StoreSpec(
            name=original.name,
            classification="immutable",
            backend="local",
            source=original.source,
            detail="external approver trust policy is malformed",
            readiness_issue="approver trust policy is malformed",
        )

    master = _feature_bool(
        config,
        section="self_improvement",
        key="enable",
        env_name="MAVERICK_SELF_IMPROVEMENT",
        default=True,
    )
    if master is False:
        return _inactive(
            original,
            "self-improvement is explicitly disabled, so no approval key is consulted",
        )

    env_keys = os.environ.get("MAVERICK_APPROVER_KEYS", "").strip()
    env_dir = os.environ.get("MAVERICK_APPROVER_KEYS_DIR", "").strip()
    env_required_raw = os.environ.get("MAVERICK_REQUIRE_SIGNED_APPROVAL")
    env_required = None
    if env_required_raw is not None:
        normalized = env_required_raw.strip().lower()
        if normalized in _TRUE_VALUES:
            env_required = True
        elif normalized in _FALSE_VALUES:
            env_required = False
    configured_keys = raw_section.get("approver_keys")
    configured_dir = raw_section.get("approver_keys_dir")
    configured_required = raw_section.get("require_signed_approval", False)
    configured_digest = raw_section.get("approver_keys_digest")
    env_digest = os.environ.get("MAVERICK_APPROVER_KEYS_DIGEST", "").strip()
    malformed_policy = (
        ("approver_keys" in raw_section and not isinstance(configured_keys, list))
        or (
            isinstance(configured_keys, list)
            and not all(isinstance(item, str) for item in configured_keys)
        )
        or (
            "approver_keys_dir" in raw_section
            and configured_dir is not None
            and not isinstance(configured_dir, str)
        )
        or (
            "approver_keys_digest" in raw_section
            and not isinstance(configured_digest, str)
        )
        or not isinstance(configured_required, bool)
        or (env_required_raw is not None and env_required is None)
    )
    key_source_configured = bool(
        env_keys
        or env_dir
        or configured_keys
        or configured_dir
        or configured_digest
        or env_digest
    )
    if env_required is False:
        trust_source_configured = key_source_configured or malformed_policy
    elif env_required is True or (
        env_required_raw is not None and env_required is None
    ):
        trust_source_configured = True
    else:
        trust_source_configured = bool(
            key_source_configured
            or configured_required is True
            or malformed_policy
        )
    if not trust_source_configured:
        return _inactive(
            original,
            "signed approval has no configured trust source or require flag",
        )

    actual_digest: str | None = None
    issue = ""
    if malformed_policy:
        issue = "approver trust policy is malformed"
    else:
        try:
            from .approval_signing import (
                approver_keyset_digest,
                resolve_approver_keys,
            )

            keys = resolve_approver_keys(raw_section)
            if not keys:
                issue = "approver trust policy resolves to no usable public keys"
            else:
                actual_digest = approver_keyset_digest(keys)
        except (OSError, TypeError, ValueError):
            issue = "approver trust policy contains an unusable public key"

    expected = (
        env_digest or _text(configured_digest)
    )
    if not issue:
        if not expected:
            issue = "deployment approver-key digest is not configured"
        elif not _SHA256_DIGEST.fullmatch(expected):
            issue = "deployment approver-key digest is malformed"
        elif expected != actual_digest:
            issue = "resolved approver keys do not match the deployment digest"

    return StoreSpec(
        name=original.name,
        classification="immutable",
        backend="local",
        source=original.source,
        detail=(
            "offline approver public-key trust set; exact deployment digest verified"
            if not issue
            else "offline approver public-key trust set is not deployment-verified"
        ),
        content_digest=actual_digest,
        readiness_issue=issue,
    )


def _conditional_store_spec(
    config: Mapping[str, Any],
    original: StoreSpec,
    *,
    governed_postgres: bool,
) -> StoreSpec | InactiveStoreSpec:
    """Apply feature/capability evidence before backend promotion rules."""
    if original.name == "deployment_encryption_key":
        return _deployment_encryption_key_spec(
            config,
            original,
            governed_postgres=governed_postgres,
        )
    if original.name == "access_policy":
        from .access_policy import policy_contract_digest

        return StoreSpec(
            name=original.name,
            classification="derived",
            backend="local",
            source=original.source,
            detail=(
                "pure decisions from frozen, content-digested resource "
                "policies and request context"
            ),
            content_digest=policy_contract_digest(),
        )
    if original.name == "evaluator_slot_epochs":
        from .evaluator_evolution import deployment_slot_authority_available

        if not deployment_slot_authority_available():
            return _inactive(
                original,
                "no built-in persistent slot registry or production activation adapter",
            )
    elif original.name == "learning_rollout_authority":
        from .learning_rollout import deployment_authority_available

        if not deployment_authority_available():
            return _inactive(
                original,
                "live rollout refuses missing caller-injected deploy/rollback backends",
            )
    elif original.name == "supply_chain_pins":
        pinning = _feature_bool(
            config,
            section="safety",
            key="supply_chain_pinning",
            env_name="MAVERICK_SUPPLY_CHAIN_PINNING",
            default=False,
        )
        if pinning is False:
            return _inactive(
                original,
                "supply-chain pin verification is explicitly or by-default disabled",
            )
    elif original.name == "approval_signing_keys":
        return _approval_input_spec(config, original)
    elif original.name == "public_form_tenant_routing":
        triggers = _feature_bool(
            config,
            section="event_triggers",
            key="enable",
            env_name="MAVERICK_EVENT_TRIGGERS",
            default=False,
        )
        tenant_by_user = _feature_bool(
            config,
            section="tenancy",
            key="by_user",
            env_name="MAVERICK_TENANT_BY_USER",
            default=False,
        )
        if triggers is False or tenant_by_user is False:
            reason = (
                "event-trigger ingress is disabled"
                if triggers is False
                else "tenant-by-user isolation is disabled"
            )
            return _inactive(original, reason)
    return original


def default_registry(
    config: Mapping[str, Any] | None = None,
    *,
    expected_stores: Iterable[str] = (),
) -> AuthorityStateRegistry:
    """Build the inventory for current implementations and declared backends."""
    cfg = config or {}
    postgres = _postgres_world_declared(cfg)
    governed_postgres = _governed_records_postgres_declared(cfg)
    harness_postgres = _self_harness_postgres_declared(cfg)
    holdout_authority_issue = (
        _holdout_authority_id_issue(cfg) if governed_postgres else None
    )
    (
        identity_policy_digest,
        scim_secret_digest,
        identity_federation_issue,
    ) = _identity_federation_inputs(cfg, governed_postgres)
    redis = _redis_queue_declared(cfg)
    registry = AuthorityStateRegistry(expected_stores=expected_stores)
    for original in _LOCAL_STORE_SPECS:
        conditional = _conditional_store_spec(
            cfg,
            original,
            governed_postgres=governed_postgres,
        )
        if isinstance(conditional, InactiveStoreSpec):
            if original.name in registry.expected_stores:
                # A positive discovery outranks feature heuristics. Register
                # the known conservative local classification instead of
                # hiding or crashing on the contradiction.
                spec = original
            else:
                registry.mark_inactive(conditional)
                continue
        else:
            spec = conditional
        if original.name in _WORLD_SHARED_STORES:
            spec = _world_shared_store_spec(
                original,
                postgres=postgres,
                redis=redis,
            )
        elif original.name == "self_harness_learning_state":
            spec = _self_harness_store_spec(
                original,
                harness_postgres=harness_postgres,
            )
        elif original.name in _IDENTITY_FEDERATION_STORES:
            spec = _identity_federation_store_spec(
                original,
                governed_postgres=governed_postgres,
                policy_digest=identity_policy_digest,
                secret_digest=scim_secret_digest,
                readiness_issue=identity_federation_issue,
            )
        elif original.name in {
            "capability_revocations",
            "agent_trust_registry",
            "runtime_overrides",
            "handoff_nonce_claims",
            "handoff_issuer_state",
            "saml_replay_claims",
            "federation_ingress_claims",
            "insight_exchange_replay_claims",
            "tool_token_replay_claims",
            "marketplace_federation_state",
            "tenant_registry",
            "tenant_policy_overlays",
            "tenant_wrapped_deks",
            "tenant_usage_ledgers",
            "tenant_spend_reservations",
            "oauth_vaults",
            "browser_auth_vaults",
            "task_graphs",
            "event_trigger_registry",
            "saas_trigger_registry",
            "adapter_activation",
            "dashboard_settings",
            "configuration_proposals",
            "fleet_agent_roster",
            "mcp_registry_overlay",
            "form_submissions",
            "public_form_tenant_routing",
            "role_overrides",
            "budget_tuning_policy",
            "self_learning_state",
            "verifier_artifact_activation",
            "verifier_confirmation_claims",
            "factory_correction_activation",
            "dgm_pack_activation",
            "calibration_freeze_state",
            "holdout_query_budget",
            "security_governance_records",
            "privacy_governance_records",
            "cra_product_security_records",
            "model_risk_assurance_records",
            "production_assurance_campaigns",
            "production_assurance_drills",
            "agent_trust_declarations",
            "agent_trust_observations",
            "evidence_graph",
            "dashboard_tenant_roles",
            "dashboard_invitations",
            "session_revocations",
        }:
            if governed_postgres and not (
                original.name == "holdout_query_budget"
                and holdout_authority_issue is not None
            ):
                detail = original.detail + _GOVERNED_STORE_DETAIL_SUFFIXES.get(
                    original.name, ""
                )
                content_digest = None
                readiness_issue = ""
                if original.name == "holdout_query_budget":
                    detail += "; tenant-scoped stable authority id configured"
                elif original.name == "tenant_policy_overlays":
                    detail += (
                        "; encrypted tenant CAS; raw external-secret references "
                        "only; legacy file digest must be explicitly imported"
                    )
                elif original.name == "tenant_wrapped_deks":
                    detail += (
                        "; deployment-key-encrypted bootstrap CAS with KMS policy "
                        "digest and convergent first-create/rotation"
                    )
                elif original.name == "oauth_vaults":
                    detail += (
                        "; tenant-scoped encrypted CAS with durable refresh-effect "
                        "claims and ambiguous-outcome refusal"
                    )
                elif original.name == "browser_auth_vaults":
                    detail += (
                        "; exact tenant/principal encrypted CAS with atomic entry "
                        "mutations"
                    )
                elif original.name == "task_graphs":
                    detail += "; tenant-scoped governed CAS document"
                elif original.name == "dashboard_tenant_roles":
                    detail += "; exact requested tenant selects the shared CAS row"
                elif original.name == "dashboard_invitations":
                    detail += (
                        "; invitation redemption and explicit global-role "
                        "assignment share one deployment-global CAS commit"
                    )
                    content_digest, readiness_issue = (
                        _invite_session_signing_proof(cfg)
                    )
                    if content_digest:
                        detail += "; local-session signing digest fleet-bound"
                elif original.name == "session_revocations":
                    detail += "; deployment-global revocation epochs use CAS"
                spec = StoreSpec(
                    name=original.name,
                    classification=original.classification,
                    backend="shared",
                    service="postgres",
                    source=original.source,
                    detail=detail,
                    content_digest=content_digest,
                    readiness_issue=readiness_issue,
                )
            elif (
                governed_postgres
                and original.name == "holdout_query_budget"
                and holdout_authority_issue is not None
            ):
                spec = StoreSpec(
                    name=original.name,
                    classification=original.classification,
                    backend="local",
                    source=original.source,
                    detail=(
                        _HOLDOUT_AUTHORITY_ISSUE_PREFIX
                        + holdout_authority_issue
                    ),
                )
        registry.register(spec)
    return registry


def _parse_replica_count(
    explicit: int | str | None,
) -> tuple[int | None, str | None]:
    if explicit is not None:
        raw: object = explicit
    elif REPLICA_COUNT_ENV in os.environ:
        raw = os.environ.get(REPLICA_COUNT_ENV)
    else:
        raw = 1
    if isinstance(raw, bool):
        return None, "replica count must be a positive integer"
    try:
        parsed = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None, "replica count must be a positive integer"
    if parsed < 1 or (isinstance(raw, float) and raw != parsed):
        return None, "replica count must be a positive integer"
    return parsed, None


def readiness_report(
    *,
    registry: AuthorityStateRegistry | None = None,
    probes: Mapping[str, Probe] | None = None,
    config: Mapping[str, Any] | None = None,
    config_errors: Mapping[str, str] | Iterable[str] | None = None,
    enterprise: bool | None = None,
    replica_count: int | str | None = None,
    probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Build the current machine-readable authority admission report.

    Explicit arguments are dependency-injection seams for tests and deployment
    wrappers.  With no arguments, configuration is loaded deployment-globally;
    unreadable policy is an enforcement reason and cannot produce a green HA
    report.
    """
    load_errors: list[str] = []
    if config is None:
        try:
            from .config import load_global_config

            loaded = load_global_config()
            if not isinstance(loaded, Mapping):
                load_errors.append("configuration root is not a mapping")
                cfg: Mapping[str, Any] = {}
            else:
                cfg = loaded
        except Exception as exc:
            load_errors.append(f"configuration load raised {type(exc).__name__}")
            cfg = {}
    elif isinstance(config, Mapping):
        cfg = config
    else:
        load_errors.append("configuration root is not a mapping")
        cfg = {}

    if config_errors is None and config is None:
        try:
            from .config import config_source_errors

            source_errors = config_source_errors(include_tenant=False)
            load_errors.extend(f"unreadable source: {path}" for path in source_errors)
        except Exception as exc:
            load_errors.append(
                f"configuration error inspection raised {type(exc).__name__}"
            )
    elif isinstance(config_errors, Mapping):
        load_errors.extend(f"unreadable source: {path}" for path in config_errors)
    elif config_errors is not None:
        load_errors.extend(str(item) for item in config_errors)

    if enterprise is None:
        try:
            from .enterprise import deployment_enterprise_enabled

            # Resolve enterprise posture from the same deployment-global
            # snapshot used by the registry and service declarations.  A live
            # tenant overlay must never weaken deployment admission.
            enterprise_mode = bool(deployment_enterprise_enabled(
                config=cfg,
                source_errors=tuple(load_errors),
            ))
        except Exception as exc:
            enterprise_mode = True
            load_errors.append(
                f"enterprise policy inspection raised {type(exc).__name__}"
            )
    else:
        enterprise_mode = bool(enterprise)

    parsed_replicas, replica_error = _parse_replica_count(replica_count)
    if replica_error:
        load_errors.append(replica_error)

    reasons: list[str] = []
    if enterprise_mode:
        reasons.append("enterprise")
    if parsed_replicas is not None and parsed_replicas > 1:
        reasons.append("replica_count>1")
    if load_errors:
        reasons.append("untrusted_configuration")

    active_registry = registry or default_registry(cfg)

    declarations = service_declarations(cfg)
    services = probe_services(
        declarations,
        probes,
        timeout_seconds=probe_timeout_seconds,
    )
    return active_registry.readiness_report(
        enterprise=enterprise_mode,
        replica_count=parsed_replicas,
        enforcement_reasons=reasons,
        services=services,
        config_errors=load_errors,
    )


def require_ready(**kwargs: Any) -> dict[str, Any]:
    """Return the report or raise when strict admission is red."""
    report = readiness_report(**kwargs)
    if not report["ready"]:
        raise AuthorityStateNotReady(report)
    return report


def compatibility_local_authority_allowed() -> bool:
    """Whether a caller-owned local mutation is safe as compatibility state.

    This is intentionally narrower than overall startup admission.  Library
    helpers whose built-in deployment authority is absent may retain their
    historical single-node behavior, but must not let an injected local object
    become hidden authority under strict identity policy or a multi-replica
    topology.
    """
    try:
        from .authority_policy import strict_authority

        if strict_authority():
            return False
    except Exception:
        return False
    replicas, issue = _parse_replica_count(None)
    if issue is not None or replicas != 1:
        return False
    try:
        # Use the same pure runtime selector as governed records without
        # process-pinning it. A single-replica Postgres deployment still has a
        # shared authority plane; policy uncertainty there must not fall back
        # to legacy local behavior merely because the replica count is one.
        from .governed_records import _selection

        return _selection().kind == "local"
    except Exception:
        return False


def deployment_guarantee() -> tuple[bool, str]:
    """Adapter used by :mod:`maverick.deployment` without circular imports."""
    report = readiness_report()
    if report["ready"]:
        if report["ha_eligible"]:
            return True, "all registered authorities are shared and live-probed"
        return True, (
            "single-replica community compatibility mode; HA eligibility is red "
            "but not enforced"
        )
    codes = ", ".join(item["code"] for item in report["blockers"])
    return False, f"authority admission blocked: {codes}"


KNOWN_STORE_NAMES = frozenset(spec.name for spec in _LOCAL_STORE_SPECS)

__all__ = [
    "AuthorityStateNotReady",
    "AuthorityStateRegistry",
    "compatibility_local_authority_allowed",
    "DEFAULT_PROBE_TIMEOUT_SECONDS",
    "InactiveStoreSpec",
    "KNOWN_STORE_NAMES",
    "REPORT_SCHEMA",
    "REPLICA_COUNT_ENV",
    "REQUIRED_SHARED_SERVICES",
    "STORE_BACKENDS",
    "STORE_CLASSES",
    "ServiceDeclaration",
    "StoreSpec",
    "default_registry",
    "deployment_guarantee",
    "probe_services",
    "readiness_report",
    "require_ready",
    "service_declarations",
]
