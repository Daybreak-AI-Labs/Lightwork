"""Shared desired-state authority for governed artifact releases.

Promotion receipts answer *whether* an artifact may be released.  This module
answers the separate fleet question: *which exact digest should each node be
running now, and has that node proved that it is healthy?*

The authority is deliberately effect-free.  It never reads artifact bytes,
changes a serving pointer, or calls a model provider.  Callers consume a pure
reconciliation decision and perform those effects through their own governed
effect protocol.  Desired releases and per-node observations use independent
governed-record CAS rows so node heartbeats do not contend on the desired-state
record.  The underlying store preserves local single-replica compatibility and
inherits the existing fail-closed Postgres selection for enterprise or
multi-replica deployments.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Final

from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

RELEASE_SCHEMA: Final = "lightwork.artifact-release-authority.v1"
OBSERVATION_SCHEMA: Final = "lightwork.artifact-node-observation.v1"

ROLLOUT_STATES: Final = frozenset({
    "pending",
    "canary",
    "rolling",
    "ready",
    "paused",
    "rollback",
    "failed",
    "retired",
})
HEALTH_STATES: Final = frozenset({
    "unknown",
    "staging",
    "healthy",
    "unhealthy",
    "offline",
})

_SERVING_STATES = frozenset({"canary", "rolling", "ready", "rollback"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DEPLOYMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_ARTIFACT_KIND_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_AUTHORITY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_MAX_GENERATION = (1 << 63) - 1
_MAX_FUTURE_SKEW_SECONDS = 300.0
_MAX_REQUIRED_NODES = 4096

_RELEASES = GovernedRecordStore(
    "artifact_release_authority",
    "ARL",
    "artifact_release_authority",
)
_OBSERVATIONS = GovernedRecordStore(
    "artifact_release_observations",
    "ARO",
    "artifact_release_observation",
)

_RELEASE_TRANSITIONS: Final = {
    "pending": frozenset({"canary", "rolling", "paused", "failed", "retired"}),
    "canary": frozenset({"rolling", "ready", "paused", "failed", "rollback"}),
    "rolling": frozenset({"ready", "paused", "failed", "rollback"}),
    "ready": frozenset({"paused", "failed", "rollback", "retired"}),
    "paused": frozenset({"pending", "canary", "rolling", "failed", "rollback", "retired"}),
    "rollback": frozenset({"canary", "rolling", "ready", "paused", "failed"}),
    "failed": frozenset({"rollback", "retired"}),
    "retired": frozenset(),
}

_STORE_FIELDS = frozenset({"revision", "created_at", "updated_at", "_audit_pending"})
_RELEASE_FIELDS = frozenset({
    "id",
    "schema",
    "record_kind",
    "deployment_id",
    "artifact_kind",
    "generation",
    "desired_digest",
    "previous_digest",
    "promotion_record_id",
    "promotion_transaction_id",
    "rollout_state",
    "status",
})
_OBSERVATION_FIELDS = frozenset({
    "id",
    "schema",
    "record_kind",
    "deployment_id",
    "artifact_kind",
    "node_id",
    "observed_generation",
    "observed_digest",
    "health",
    "last_error",
    "observed_at",
    "status",
})


class ArtifactReleaseError(RuntimeError):
    """The artifact release authority could not preserve its contract."""


class ArtifactReleaseConflict(ArtifactReleaseError):
    """A caller lost a desired-state or observation CAS race."""


class ArtifactReleaseStateError(ArtifactReleaseError):
    """Persisted release authority is malformed or internally inconsistent."""


class ReconciliationAction(str, Enum):
    """Pure instruction emitted for a node reconciler; no action runs here."""

    WAIT_FOR_DESIRED = "wait_for_desired"
    AUTHORITY_CHANGED = "authority_changed"
    HOLD = "hold"
    APPLY_DESIRED = "apply_desired"
    VERIFY_HEALTH = "verify_health"
    AWAIT_ROLLOUT = "await_rollout"
    RETIRE_LOCAL = "retire_local"
    SERVE = "serve"


@dataclass(frozen=True)
class DesiredRelease:
    record_id: str
    deployment_id: str
    artifact_kind: str
    generation: int
    desired_digest: str
    previous_digest: str | None
    promotion_record_id: str
    promotion_transaction_id: str
    rollout_state: str
    revision: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class NodeObservation:
    record_id: str
    deployment_id: str
    artifact_kind: str
    node_id: str
    observed_generation: int
    observed_digest: str | None
    health: str
    last_error: str
    observed_at: float
    revision: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ReconciliationDecision:
    deployment_id: str
    node_id: str
    action: ReconciliationAction
    reason: str
    desired_generation: int | None
    desired_digest: str | None
    rollout_state: str | None
    observed_generation: int | None
    observed_digest: str | None
    health: str | None
    ready: bool


@dataclass(frozen=True)
class FleetReadiness:
    deployment_id: str
    desired_generation: int | None
    desired_digest: str | None
    rollout_state: str | None
    consistent_snapshot: bool
    ready: bool
    decisions: tuple[ReconciliationDecision, ...]


def _bounded_identity(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or value != value.strip() or not pattern.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _deployment_id(value: object) -> str:
    return _bounded_identity(value, "deployment_id", _DEPLOYMENT_RE)


def _artifact_kind(value: object) -> str:
    return _bounded_identity(value, "artifact_kind", _ARTIFACT_KIND_RE)


def _node_id(value: object) -> str:
    return _bounded_identity(value, "node_id", _NODE_RE)


def _authority_id(value: object, label: str) -> str:
    return _bounded_identity(value, label, _AUTHORITY_ID_RE)


def _digest(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= _MAX_GENERATION
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _finite_timestamp(value: object, label: str, *, positive: bool = True) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{label} is invalid")
    return result


def _release_record_id(deployment_id: str) -> str:
    suffix = hashlib.sha256(("desired\0" + deployment_id).encode("utf-8")).hexdigest()
    return "ARL-" + suffix[:60]


def _observation_record_id(deployment_id: str, node_id: str) -> str:
    suffix = hashlib.sha256(
        ("observed\0" + deployment_id + "\0" + node_id).encode("utf-8")
    ).hexdigest()
    return "ARO-" + suffix[:60]


def _safe_error(value: object) -> str:
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - untrusted diagnostic objects fail closed
        return "[diagnostic unavailable]"
    try:
        from .safety.secret_detector import redact

        text, _matches = redact(text)
    except Exception:  # noqa: BLE001 - never persist an unscanned diagnostic
        return "[diagnostic redaction unavailable]"
    text = " ".join(text.split())
    return text[:1024]


def _stored_revision(value: object) -> int:
    try:
        return _integer(value, "revision", minimum=1)
    except ValueError as exc:
        raise ArtifactReleaseStateError("stored revision is invalid") from exc


def _stored_timestamp(value: object, label: str) -> float:
    try:
        return _finite_timestamp(value, label)
    except ValueError as exc:
        raise ArtifactReleaseStateError(f"stored {label} is invalid") from exc


def _parse_release(raw: object, deployment: str) -> DesiredRelease:
    if not isinstance(raw, dict):
        raise ArtifactReleaseStateError("desired release record is not an object")
    if set(raw).difference(_RELEASE_FIELDS | _STORE_FIELDS):
        raise ArtifactReleaseStateError("desired release record has unknown fields")
    try:
        stored_deployment = _deployment_id(raw.get("deployment_id"))
        artifact_kind = _artifact_kind(raw.get("artifact_kind"))
        generation = _integer(raw.get("generation"), "generation", minimum=1)
        desired_digest = _digest(raw.get("desired_digest"), "desired_digest")
        previous_digest = _digest(
            raw.get("previous_digest"), "previous_digest", optional=True
        )
        promotion_record_id = _authority_id(
            raw.get("promotion_record_id"), "promotion_record_id"
        )
        promotion_transaction_id = _authority_id(
            raw.get("promotion_transaction_id"), "promotion_transaction_id"
        )
    except ValueError as exc:
        raise ArtifactReleaseStateError("desired release record is malformed") from exc
    state = raw.get("rollout_state")
    if (
        raw.get("schema") != RELEASE_SCHEMA
        or raw.get("record_kind") != "desired"
        or state not in ROLLOUT_STATES
        or raw.get("status") != state
        or stored_deployment != deployment
    ):
        raise ArtifactReleaseStateError("desired release record binding is invalid")
    expected_id = _release_record_id(deployment)
    if raw.get("id") != expected_id:
        raise ArtifactReleaseStateError("desired release identity is invalid")
    if (generation == 1) != (previous_digest is None):
        raise ArtifactReleaseStateError("desired release lineage is invalid")
    return DesiredRelease(
        record_id=expected_id,
        deployment_id=stored_deployment,
        artifact_kind=artifact_kind,
        generation=generation,
        desired_digest=str(desired_digest),
        previous_digest=previous_digest,
        promotion_record_id=promotion_record_id,
        promotion_transaction_id=promotion_transaction_id,
        rollout_state=str(state),
        revision=_stored_revision(raw.get("revision")),
        created_at=_stored_timestamp(raw.get("created_at"), "created_at"),
        updated_at=_stored_timestamp(raw.get("updated_at"), "updated_at"),
    )


def _parse_observation(raw: object, deployment: str, node: str) -> NodeObservation:
    if not isinstance(raw, dict):
        raise ArtifactReleaseStateError("node observation record is not an object")
    if set(raw).difference(_OBSERVATION_FIELDS | _STORE_FIELDS):
        raise ArtifactReleaseStateError("node observation record has unknown fields")
    try:
        stored_deployment = _deployment_id(raw.get("deployment_id"))
        stored_node = _node_id(raw.get("node_id"))
        artifact_kind = _artifact_kind(raw.get("artifact_kind"))
        generation = _integer(
            raw.get("observed_generation"), "observed_generation", minimum=0
        )
        observed_digest = _digest(
            raw.get("observed_digest"), "observed_digest", optional=True
        )
        observed_at = _finite_timestamp(raw.get("observed_at"), "observed_at")
    except ValueError as exc:
        raise ArtifactReleaseStateError("node observation record is malformed") from exc
    health = raw.get("health")
    if (
        raw.get("schema") != OBSERVATION_SCHEMA
        or raw.get("record_kind") != "observation"
        or health not in HEALTH_STATES
        or raw.get("status") != health
        or stored_deployment != deployment
        or stored_node != node
    ):
        raise ArtifactReleaseStateError("node observation binding is invalid")
    expected_id = _observation_record_id(deployment, node)
    if raw.get("id") != expected_id:
        raise ArtifactReleaseStateError("node observation identity is invalid")
    if (generation == 0) != (observed_digest is None):
        raise ArtifactReleaseStateError("node observation generation is inconsistent")
    last_error = raw.get("last_error")
    if not isinstance(last_error, str) or len(last_error) > 1024:
        raise ArtifactReleaseStateError("node observation diagnostic is invalid")
    if health == "healthy" and (observed_digest is None or last_error):
        raise ArtifactReleaseStateError("healthy node observation is contradictory")
    return NodeObservation(
        record_id=expected_id,
        deployment_id=stored_deployment,
        artifact_kind=artifact_kind,
        node_id=stored_node,
        observed_generation=generation,
        observed_digest=observed_digest,
        health=str(health),
        last_error=last_error,
        observed_at=observed_at,
        revision=_stored_revision(raw.get("revision")),
        created_at=_stored_timestamp(raw.get("created_at"), "created_at"),
        updated_at=_stored_timestamp(raw.get("updated_at"), "updated_at"),
    )


def _same_release_attempt(
    current: DesiredRelease,
    *,
    artifact_kind: str,
    desired_digest: str,
    promotion_record_id: str,
    promotion_transaction_id: str,
) -> bool:
    if current.promotion_transaction_id != promotion_transaction_id:
        return False
    if (
        current.artifact_kind != artifact_kind
        or current.desired_digest != desired_digest
        or current.promotion_record_id != promotion_record_id
    ):
        raise ArtifactReleaseConflict(
            "promotion transaction id was reused with different release intent"
        )
    return True


def _decision(
    desired: DesiredRelease | None,
    observation: NodeObservation | None,
    *,
    deployment_id: str,
    node_id: str,
) -> ReconciliationDecision:
    observed_generation = (
        observation.observed_generation if observation is not None else None
    )
    observed_digest = observation.observed_digest if observation is not None else None
    health = observation.health if observation is not None else None
    base = {
        "deployment_id": deployment_id,
        "node_id": node_id,
        "desired_generation": desired.generation if desired is not None else None,
        "desired_digest": desired.desired_digest if desired is not None else None,
        "rollout_state": desired.rollout_state if desired is not None else None,
        "observed_generation": observed_generation,
        "observed_digest": observed_digest,
        "health": health,
    }
    if desired is None:
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.WAIT_FOR_DESIRED,
            reason="no desired release exists",
            ready=False,
        )
    if desired.rollout_state == "retired":
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.RETIRE_LOCAL,
            reason="release is retired",
            ready=False,
        )
    if desired.rollout_state in {"paused", "failed"}:
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.HOLD,
            reason=f"rollout is {desired.rollout_state}",
            ready=False,
        )
    if (
        observation is None
        or observation.artifact_kind != desired.artifact_kind
        or observation.observed_generation != desired.generation
        or observation.observed_digest != desired.desired_digest
    ):
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.APPLY_DESIRED,
            reason="node does not report the exact desired generation and digest",
            ready=False,
        )
    if observation.health != "healthy":
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.VERIFY_HEALTH,
            reason=f"desired bytes are present but node health is {observation.health}",
            ready=False,
        )
    if desired.rollout_state not in _SERVING_STATES:
        return ReconciliationDecision(
            **base,
            action=ReconciliationAction.AWAIT_ROLLOUT,
            reason="desired artifact is healthy but rollout has not admitted serving",
            ready=False,
        )
    return ReconciliationDecision(
        **base,
        action=ReconciliationAction.SERVE,
        reason="healthy observed generation and digest match desired authority",
        ready=True,
    )


def _authority_changed_decision(
    desired: DesiredRelease | None,
    observation: NodeObservation | None,
    *,
    deployment_id: str,
    node_id: str,
) -> ReconciliationDecision:
    return ReconciliationDecision(
        deployment_id=deployment_id,
        node_id=node_id,
        action=ReconciliationAction.AUTHORITY_CHANGED,
        reason="desired authority changed while observations were read",
        desired_generation=desired.generation if desired is not None else None,
        desired_digest=desired.desired_digest if desired is not None else None,
        rollout_state=desired.rollout_state if desired is not None else None,
        observed_generation=(
            observation.observed_generation if observation is not None else None
        ),
        observed_digest=observation.observed_digest if observation is not None else None,
        health=observation.health if observation is not None else None,
        ready=False,
    )


class ArtifactReleaseAuthority:
    """Tenant-scoped desired releases and independent node observations."""

    def __init__(
        self,
        *,
        release_store: GovernedRecordStore | None = None,
        observation_store: GovernedRecordStore | None = None,
        clock=time.time,
    ) -> None:
        self._releases = release_store or _RELEASES
        self._observations = observation_store or _OBSERVATIONS
        if not callable(clock):
            raise ValueError("clock must be callable")
        self._clock = clock

    def _ensure_backend_alignment(self) -> str:
        release_kind = self._releases.backend_kind
        observation_kind = self._observations.backend_kind
        if release_kind != observation_kind:
            raise ArtifactReleaseStateError(
                "desired releases and node observations use different backends"
            )
        return release_kind

    @property
    def backend_kind(self) -> str:
        return self._ensure_backend_alignment()

    def get_desired(self, deployment_id: str) -> DesiredRelease | None:
        deployment = _deployment_id(deployment_id)
        self._ensure_backend_alignment()
        raw = self._releases.get(_release_record_id(deployment))
        return None if raw is None else _parse_release(raw, deployment)

    def list_desired(self) -> tuple[DesiredRelease, ...]:
        self._ensure_backend_alignment()
        out: list[DesiredRelease] = []
        for raw in self._releases.list():
            if not isinstance(raw, dict):
                raise ArtifactReleaseStateError("desired release record is not an object")
            deployment = raw.get("deployment_id")
            try:
                validated = _deployment_id(deployment)
            except ValueError as exc:
                raise ArtifactReleaseStateError(
                    "desired release record has an invalid deployment id"
                ) from exc
            out.append(_parse_release(raw, validated))
        return tuple(sorted(out, key=lambda item: item.deployment_id))

    def publish_desired(
        self,
        deployment_id: str,
        *,
        artifact_kind: str,
        desired_digest: str,
        promotion_record_id: str,
        promotion_transaction_id: str,
        expected_revision: int,
        actor: str,
    ) -> DesiredRelease:
        """Create a desired generation or CAS-publish its exact successor.

        Retrying the same promotion transaction with the same intent is
        idempotent, including after a lost response.  Reusing that transaction
        id with different intent fails closed.
        """
        deployment = _deployment_id(deployment_id)
        kind = _artifact_kind(artifact_kind)
        digest = str(_digest(desired_digest, "desired_digest"))
        record_id = _authority_id(promotion_record_id, "promotion_record_id")
        transaction_id = _authority_id(
            promotion_transaction_id, "promotion_transaction_id"
        )
        expected = _integer(expected_revision, "expected_revision", minimum=0)
        self._ensure_backend_alignment()
        current = self.get_desired(deployment)
        if current is not None and _same_release_attempt(
            current,
            artifact_kind=kind,
            desired_digest=digest,
            promotion_record_id=record_id,
            promotion_transaction_id=transaction_id,
        ):
            return current
        current_revision = current.revision if current is not None else 0
        if expected != current_revision:
            raise ArtifactReleaseConflict(
                f"desired release changed (expected revision {expected}, "
                f"found {current_revision})"
            )
        if current is not None and current.artifact_kind != kind:
            raise ArtifactReleaseConflict("artifact kind cannot change within a deployment")
        generation = 1 if current is None else current.generation + 1
        if generation > _MAX_GENERATION:
            raise ArtifactReleaseStateError("desired generation is exhausted")
        previous = current.desired_digest if current is not None else None
        rollout_state = (
            "rollback"
            if current is not None and digest == current.previous_digest
            else "pending"
        )
        logical = {
            "id": _release_record_id(deployment),
            "schema": RELEASE_SCHEMA,
            "record_kind": "desired",
            "deployment_id": deployment,
            "artifact_kind": kind,
            "generation": generation,
            "desired_digest": digest,
            "previous_digest": previous,
            "promotion_record_id": record_id,
            "promotion_transaction_id": transaction_id,
            "rollout_state": rollout_state,
            "status": rollout_state,
        }
        try:
            if current is None:
                saved = self._releases.create(
                    logical,
                    action="publish_desired",
                    actor=actor,
                )
            else:
                def mutate(row: dict) -> None:
                    for key, value in logical.items():
                        if key != "id":
                            row[key] = value

                saved = self._releases.update(
                    logical["id"],
                    mutate,
                    expected_revision=expected,
                    action="publish_desired",
                    actor=actor,
                )
                if saved is None:
                    raise ArtifactReleaseStateError(
                        "desired release disappeared during publication"
                    )
        except RecordConflict as exc:
            winner = self.get_desired(deployment)
            if winner is not None and _same_release_attempt(
                winner,
                artifact_kind=kind,
                desired_digest=digest,
                promotion_record_id=record_id,
                promotion_transaction_id=transaction_id,
            ):
                return winner
            raise ArtifactReleaseConflict("desired release CAS conflict") from exc
        return _parse_release(saved, deployment)

    def transition_rollout(
        self,
        deployment_id: str,
        rollout_state: str,
        *,
        expected_revision: int,
        actor: str,
    ) -> DesiredRelease:
        deployment = _deployment_id(deployment_id)
        if not isinstance(rollout_state, str) or rollout_state not in ROLLOUT_STATES:
            raise ValueError("rollout_state is invalid")
        expected = _integer(expected_revision, "expected_revision", minimum=0)
        self._ensure_backend_alignment()
        current = self.get_desired(deployment)
        if current is None:
            raise ArtifactReleaseStateError("desired release does not exist")
        if expected != current.revision:
            raise ArtifactReleaseConflict(
                f"desired release changed (expected revision {expected}, "
                f"found {current.revision})"
            )
        if current.rollout_state == rollout_state:
            return current
        if rollout_state not in _RELEASE_TRANSITIONS[current.rollout_state]:
            raise ArtifactReleaseStateError(
                f"rollout transition {current.rollout_state} -> {rollout_state} is invalid"
            )

        def mutate(row: dict) -> None:
            row["rollout_state"] = rollout_state
            row["status"] = rollout_state

        try:
            saved = self._releases.update(
                current.record_id,
                mutate,
                expected_revision=expected,
                action="transition_rollout",
                actor=actor,
            )
        except RecordConflict as exc:
            raise ArtifactReleaseConflict("rollout-state CAS conflict") from exc
        if saved is None:
            raise ArtifactReleaseStateError("desired release disappeared during transition")
        return _parse_release(saved, deployment)

    def get_observation(
        self,
        deployment_id: str,
        node_id: str,
    ) -> NodeObservation | None:
        deployment = _deployment_id(deployment_id)
        node = _node_id(node_id)
        self._ensure_backend_alignment()
        raw = self._observations.get(_observation_record_id(deployment, node))
        return None if raw is None else _parse_observation(raw, deployment, node)

    def list_observations(self, deployment_id: str) -> tuple[NodeObservation, ...]:
        deployment = _deployment_id(deployment_id)
        self._ensure_backend_alignment()
        out: list[NodeObservation] = []
        for raw in self._observations.list():
            if not isinstance(raw, dict):
                raise ArtifactReleaseStateError("node observation record is not an object")
            if raw.get("deployment_id") != deployment:
                continue
            try:
                node = _node_id(raw.get("node_id"))
            except ValueError as exc:
                raise ArtifactReleaseStateError(
                    "node observation has an invalid node id"
                ) from exc
            out.append(_parse_observation(raw, deployment, node))
        return tuple(sorted(out, key=lambda item: item.node_id))

    def report_observation(
        self,
        deployment_id: str,
        node_id: str,
        *,
        observed_generation: int,
        observed_digest: str | None,
        health: str,
        last_error: object = "",
        observed_at: float | None = None,
        expected_revision: int,
        actor: str,
    ) -> NodeObservation:
        deployment = _deployment_id(deployment_id)
        node = _node_id(node_id)
        generation = _integer(
            observed_generation, "observed_generation", minimum=0
        )
        digest = _digest(observed_digest, "observed_digest", optional=True)
        if not isinstance(health, str) or health not in HEALTH_STATES:
            raise ValueError("health is invalid")
        diagnostic = _safe_error(last_error)
        if (generation == 0) != (digest is None):
            raise ValueError("observed generation and digest are inconsistent")
        if health == "healthy" and (digest is None or diagnostic):
            raise ValueError(
                "healthy observation requires an artifact and cannot carry last_error"
            )
        # A node clock is adequate for the local compatibility plane, but a
        # shared observation timestamp participates in cross-replica ordering.
        # Use the selected database clock there so skewed workers cannot publish
        # observations that move authority time forwards or backwards.
        shared_backend = self._ensure_backend_alignment() == "postgres"
        if shared_backend:
            now = _finite_timestamp(
                self._observations.authoritative_time(), "authority clock",
            )
        else:
            now = _finite_timestamp(self._clock(), "clock")
        # In the shared plane, caller/node time is never ordering authority.
        # ``observed_at`` remains a compatibility input for the local plane.
        timestamp = now if shared_backend or observed_at is None else (
            _finite_timestamp(observed_at, "observed_at")
        )
        if timestamp > now + _MAX_FUTURE_SKEW_SECONDS:
            raise ValueError("observed_at is too far in the future")
        expected = _integer(expected_revision, "expected_revision", minimum=0)
        desired = self.get_desired(deployment)
        if desired is None:
            raise ArtifactReleaseStateError("cannot observe a deployment with no desired release")
        if generation > desired.generation:
            raise ArtifactReleaseStateError("node cannot observe a future desired generation")
        current = self.get_observation(deployment, node)
        current_revision = current.revision if current is not None else 0
        if expected != current_revision:
            raise ArtifactReleaseConflict(
                f"node observation changed (expected revision {expected}, "
                f"found {current_revision})"
            )
        if current is not None and timestamp < current.observed_at:
            raise ArtifactReleaseConflict(
                "node observation timestamp cannot move backwards"
            )
        logical = {
            "id": _observation_record_id(deployment, node),
            "schema": OBSERVATION_SCHEMA,
            "record_kind": "observation",
            "deployment_id": deployment,
            "artifact_kind": desired.artifact_kind,
            "node_id": node,
            "observed_generation": generation,
            "observed_digest": digest,
            "health": health,
            "last_error": diagnostic,
            "observed_at": timestamp,
            "status": health,
        }
        try:
            if current is None:
                saved = self._observations.create(
                    logical,
                    action="report_observation",
                    actor=actor,
                )
            else:
                def mutate(row: dict) -> None:
                    for key, value in logical.items():
                        if key != "id":
                            row[key] = value

                saved = self._observations.update(
                    logical["id"],
                    mutate,
                    expected_revision=expected,
                    action="report_observation",
                    actor=actor,
                )
                if saved is None:
                    raise ArtifactReleaseStateError(
                        "node observation disappeared during update"
                    )
        except RecordConflict as exc:
            raise ArtifactReleaseConflict("node observation CAS conflict") from exc
        return _parse_observation(saved, deployment, node)

    def reconciliation_decision(
        self,
        deployment_id: str,
        node_id: str,
    ) -> ReconciliationDecision:
        """Return a race-safe, deterministic instruction without running it."""
        deployment = _deployment_id(deployment_id)
        node = _node_id(node_id)
        desired_before = self.get_desired(deployment)
        observation = self.get_observation(deployment, node)
        desired_after = self.get_desired(deployment)
        before_revision = desired_before.revision if desired_before is not None else None
        after_revision = desired_after.revision if desired_after is not None else None
        if before_revision != after_revision:
            return _authority_changed_decision(
                desired_after,
                observation,
                deployment_id=deployment,
                node_id=node,
            )
        return _decision(
            desired_after,
            observation,
            deployment_id=deployment,
            node_id=node,
        )

    def serving_ready(self, deployment_id: str, node_id: str) -> bool:
        """True only for an admitted, healthy exact desired generation/digest."""
        return self.reconciliation_decision(deployment_id, node_id).ready

    def fleet_readiness(
        self,
        deployment_id: str,
        required_nodes: list[str] | tuple[str, ...] | set[str],
    ) -> FleetReadiness:
        """Evaluate a stable desired revision against a deterministic node set."""
        deployment = _deployment_id(deployment_id)
        if not isinstance(required_nodes, (list, tuple, set)):
            raise ValueError("required_nodes must be a list, tuple, or set")
        if not required_nodes or len(required_nodes) > _MAX_REQUIRED_NODES:
            raise ValueError("required_nodes must contain 1-4096 nodes")
        nodes = tuple(sorted({_node_id(node) for node in required_nodes}))
        if len(nodes) != len(required_nodes):
            raise ValueError("required_nodes must not contain duplicates")
        desired_before = self.get_desired(deployment)
        observations = {
            item.node_id: item for item in self.list_observations(deployment)
            if item.node_id in nodes
        }
        desired_after = self.get_desired(deployment)
        before_revision = desired_before.revision if desired_before is not None else None
        after_revision = desired_after.revision if desired_after is not None else None
        stable = before_revision == after_revision
        decisions = tuple(
            (
                _decision(
                    desired_after,
                    observations.get(node),
                    deployment_id=deployment,
                    node_id=node,
                )
                if stable
                else _authority_changed_decision(
                    desired_after,
                    observations.get(node),
                    deployment_id=deployment,
                    node_id=node,
                )
            )
            for node in nodes
        )
        return FleetReadiness(
            deployment_id=deployment,
            desired_generation=(
                desired_after.generation if desired_after is not None else None
            ),
            desired_digest=(
                desired_after.desired_digest if desired_after is not None else None
            ),
            rollout_state=(
                desired_after.rollout_state if desired_after is not None else None
            ),
            consistent_snapshot=stable,
            ready=stable and bool(decisions) and all(item.ready for item in decisions),
            decisions=decisions,
        )


__all__ = [
    "ArtifactReleaseAuthority",
    "ArtifactReleaseConflict",
    "ArtifactReleaseError",
    "ArtifactReleaseStateError",
    "DesiredRelease",
    "FleetReadiness",
    "HEALTH_STATES",
    "NodeObservation",
    "OBSERVATION_SCHEMA",
    "RELEASE_SCHEMA",
    "ROLLOUT_STATES",
    "ReconciliationAction",
    "ReconciliationDecision",
]
