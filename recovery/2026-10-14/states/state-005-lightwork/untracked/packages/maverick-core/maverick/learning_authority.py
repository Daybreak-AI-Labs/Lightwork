"""Shared authority primitives for learned-state and artifact activation.

The self-improvement promotion ledger proves that an artifact passed its gate.
It does not, by itself, tell every replica which digest is live or prove that a
particular node has those exact bytes.  This module binds the learning surfaces
to the generic :mod:`maverick.artifact_release_authority` desired-state plane and
provides the two small mutable authorities that are not artifacts:

* the bounded learned-capability admission ledger; and
* single-use verifier confirmation-cohort claims.

Local, single-replica deployments keep their historical file stores.  Callers
enter this module only after the governed-record selector chooses Postgres.
Every shared mutation is tenant-scoped, CAS-backed, encrypted by the governed
record backend, and uses the backend clock where a logical timestamp is stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from .authority_documents import AuthorityDocumentStore
from .governed_records import GovernedRecordStore
from .privacy_ops import RecordConflict

LEARNING_RELEASE_NODE_ENV: Final = "MAVERICK_LEARNING_RELEASE_NODE_ID"
SELF_LEARNING_SCHEMA: Final = "lightwork.self-learning-authority.v1"
CONFIRMATION_SCHEMA: Final = "lightwork.verifier-confirmation-claim.v1"

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_KIND_RE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_DGM_PACK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_MAX_SELF_LEARNING_ENTRIES = 20_000
_MAX_SELF_LEARNING_DOCUMENT_BYTES = 12 * 1024 * 1024
_MAX_OBSERVATION_ID = 1024
_MAX_RELEASE_RETRIES = 16
_STORE_FIELDS = frozenset({"revision", "created_at", "updated_at", "_audit_pending"})
_CONFIRMATION_FIELDS = frozenset({
    "id",
    "schema",
    "record_kind",
    "cohort_sha256",
    "before",
    "challenger",
    "intent_sha256",
    "consumed_at",
    "status",
})


class LearningAuthorityError(RuntimeError):
    """A shared learning authority could not preserve its contract."""


class LearningAuthorityConflict(LearningAuthorityError):
    """A retry identity was reused for different learning intent."""


@dataclass(frozen=True)
class LearningRelease:
    """Bounded result of publishing and observing one learned artifact."""

    deployment_id: str
    generation: int
    desired_digest: str
    previous_digest: str | None
    rollout_state: str
    node_id: str
    ready: bool


@dataclass(frozen=True)
class _ReleaseIdentity:
    """Immutable identity of one desired-release generation.

    Rollout state, record revision, and timestamps are intentionally excluded:
    they may advance while multiple replicas converge the *same* release.  Every
    field below must remain fixed across a retry, otherwise that retry would act
    on a successor or a republished predecessor rather than its original intent.
    """

    record_id: str
    deployment_id: str
    artifact_kind: str
    generation: int
    desired_digest: str
    previous_digest: str | None
    promotion_record_id: str
    promotion_transaction_id: str


_SELF_LEARNING = AuthorityDocumentStore(
    "self_learning_state",
    "SLS",
    "self_learning_state",
    required_backend="postgres",
)
_CONFIRMATION_CLAIMS = GovernedRecordStore(
    "verifier_confirmation_claims",
    "VCC",
    "verifier_confirmation_claim",
    required_backend="postgres",
)


def shared_learning_authority_selected() -> bool:
    """Return whether learned authorities are configured on Postgres.

    The selector is process-sticky and raises if deployment policy is malformed
    or drifts.  Callers must not translate that failure into local fallback.
    """
    from .governed_records import configured_governed_records_backend

    return configured_governed_records_backend() == "postgres"


def release_node_id(value: object = None) -> str:
    """Resolve the stable identity used for this node's artifact observation."""
    if value is None:
        value = os.environ.get(LEARNING_RELEASE_NODE_ENV) or socket.gethostname()
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not _NODE_RE.fullmatch(value)
    ):
        raise LearningAuthorityError(
            f"{LEARNING_RELEASE_NODE_ENV} must be a stable 1-128 character node id"
        )
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LearningAuthorityError(
            "learning authority intent is not canonical finite JSON"
        ) from exc


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise LearningAuthorityError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _artifact_kind(value: object) -> str:
    if not isinstance(value, str) or not _KIND_RE.fullmatch(value):
        raise LearningAuthorityError("artifact kind is invalid")
    return value


def learning_deployment_id(family: str, logical_name: str = "default") -> str:
    """Return a stable, bounded deployment id without persisting raw names."""
    kind = _artifact_kind(family)
    if (
        not isinstance(logical_name, str)
        or not logical_name
        or len(logical_name) > 1024
        or any(ord(char) < 32 or ord(char) == 127 for char in logical_name)
    ):
        raise LearningAuthorityError("learning deployment logical name is invalid")
    if kind == "dgm_pack":
        if not _DGM_PACK_RE.fullmatch(logical_name):
            raise LearningAuthorityError("DGM pack deployment name is invalid")
        return f"learning/dgm_pack/{logical_name}"
    suffix = _sha256((kind + "\0" + logical_name).encode("utf-8"))
    return f"learning/{kind}/{suffix[:40]}"


def release_intent_ids(
    family: str,
    *,
    digest: str,
    provenance: Mapping[str, object],
) -> tuple[str, str]:
    """Return stable record/transaction ids bound to exact release intent."""
    kind = _artifact_kind(family)
    exact_digest = _digest(digest, "release digest")
    intent = _sha256(_canonical({
        "schema": "lightwork.learning-release-intent.v1",
        "family": kind,
        "digest": exact_digest,
        "provenance": dict(provenance),
    }))
    return f"learning-record-{intent[:48]}", f"learning-txn-{intent[:51]}"


def promotion_authority_provenance(
    ledger: object,
    *,
    artifact_identity: str,
) -> dict[str, object]:
    """Bind a release to the exact promotion chain for one artifact.

    The ledger-wide revision also changes when an unrelated artifact is
    promoted.  Using it as a release identity would create a fresh deployment
    generation for unchanged bytes.  This fingerprint includes only the
    immutable transaction edges and rollback state that determine this
    artifact's current authority.
    """
    try:
        locator = ledger.authority_locator()
        transactions = ledger.transactions(
            artifact_identity=artifact_identity,
            state="committed",
        )
        chain = [
            {
                "transaction_id": transaction.id,
                "record_id": transaction.record.id,
                "rolled_back": transaction.record.rolled_back,
                "before": transaction.before.to_dict(),
                "after": transaction.after.to_dict(),
            }
            for transaction in transactions
        ]
    except Exception as exc:
        raise LearningAuthorityError(
            "promotion authority provenance is unavailable"
        ) from exc
    predecessor = None
    if transactions:
        latest = transactions[-1]
        predecessor = (
            latest.after.sha256
            if latest.record.rolled_back
            else latest.before.sha256
        )
    return {
        "ledger": locator,
        "artifact_identity": artifact_identity,
        "promotion_chain_sha256": _sha256(_canonical(chain)),
        "release_predecessor_sha256": predecessor,
    }


def _release_authority(value: object = None):
    if value is None:
        from .artifact_release_authority import ArtifactReleaseAuthority

        value = ArtifactReleaseAuthority()
    try:
        backend = value.backend_kind
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learning release authority is unavailable"
        ) from exc
    if backend != "postgres":
        raise LearningAuthorityError(
            "shared learning release authority must use Postgres"
        )
    return value


def _release_identity(value: object | None) -> _ReleaseIdentity | None:
    if value is None:
        return None
    try:
        return _ReleaseIdentity(
            record_id=value.record_id,
            deployment_id=value.deployment_id,
            artifact_kind=value.artifact_kind,
            generation=value.generation,
            desired_digest=value.desired_digest,
            previous_digest=value.previous_digest,
            promotion_record_id=value.promotion_record_id,
            promotion_transaction_id=value.promotion_transaction_id,
        )
    except (AttributeError, TypeError) as exc:
        raise LearningAuthorityError(
            "shared learned-artifact release identity is malformed"
        ) from exc


def _require_release_identity(
    value: object | None,
    expected: _ReleaseIdentity,
    *,
    operation: str,
) -> None:
    if _release_identity(value) != expected:
        raise LearningAuthorityConflict(
            f"shared learned-artifact release identity changed during {operation}"
        )


def _is_requested_release(
    value: object | None,
    *,
    deployment_id: str,
    artifact_kind: str,
    digest: str,
    promotion_record_id: str,
    promotion_transaction_id: str,
    expected_previous_digest: str | None,
    allow_initial_cutover: bool,
) -> bool:
    """Return whether ``value`` is the exact idempotent publication attempt."""

    if value is None or value.promotion_transaction_id != promotion_transaction_id:
        return False
    previous_matches = value.previous_digest == expected_previous_digest
    if allow_initial_cutover and value.previous_digest is None:
        previous_matches = True
    if (
        value.deployment_id != deployment_id
        or value.artifact_kind != artifact_kind
        or value.desired_digest != digest
        or value.promotion_record_id != promotion_record_id
        or not previous_matches
    ):
        raise LearningAuthorityConflict(
            "learning release transaction id was reused with different intent"
        )
    return True


def _validate_release_predecessor(
    current: object | None,
    *,
    expected_previous_digest: str | None,
    allow_initial_cutover: bool,
) -> None:
    if current is None:
        if expected_previous_digest is not None and not allow_initial_cutover:
            raise LearningAuthorityConflict(
                "shared learned-artifact predecessor is missing"
            )
        return
    if (
        expected_previous_digest is None
        or current.desired_digest != expected_previous_digest
    ):
        raise LearningAuthorityConflict(
            "shared learned-artifact predecessor digest changed"
        )


def _publish_release(
    authority,
    *,
    deployment_id: str,
    artifact_kind: str,
    digest: str,
    promotion_record_id: str,
    promotion_transaction_id: str,
    expected_previous_digest: str | None,
    allow_initial_cutover: bool,
    actor: str,
):
    from .artifact_release_authority import ArtifactReleaseConflict

    unset = object()
    predecessor_identity: _ReleaseIdentity | None | object = unset
    for _attempt in range(_MAX_RELEASE_RETRIES):
        try:
            current = authority.get_desired(deployment_id)
            if _is_requested_release(
                current,
                deployment_id=deployment_id,
                artifact_kind=artifact_kind,
                digest=digest,
                promotion_record_id=promotion_record_id,
                promotion_transaction_id=promotion_transaction_id,
                expected_previous_digest=expected_previous_digest,
                allow_initial_cutover=allow_initial_cutover,
            ):
                desired = current
                break
            current_identity = _release_identity(current)
            if predecessor_identity is unset:
                predecessor_identity = current_identity
            elif current_identity != predecessor_identity:
                raise LearningAuthorityConflict(
                    "shared learned-artifact predecessor identity changed during "
                    "publication"
                )
            _validate_release_predecessor(
                current,
                expected_previous_digest=expected_previous_digest,
                allow_initial_cutover=allow_initial_cutover,
            )
            desired = authority.publish_desired(
                deployment_id,
                artifact_kind=artifact_kind,
                desired_digest=digest,
                promotion_record_id=promotion_record_id,
                promotion_transaction_id=promotion_transaction_id,
                expected_revision=current.revision if current is not None else 0,
                actor=actor,
            )
            if not _is_requested_release(
                desired,
                deployment_id=deployment_id,
                artifact_kind=artifact_kind,
                digest=digest,
                promotion_record_id=promotion_record_id,
                promotion_transaction_id=promotion_transaction_id,
                expected_previous_digest=expected_previous_digest,
                allow_initial_cutover=allow_initial_cutover,
            ):
                raise LearningAuthorityConflict(
                    "shared learned-artifact publication returned a different release"
                )
        except ArtifactReleaseConflict as exc:
            # A rollout-only revision race may leave the exact same predecessor
            # in place. Re-read and retry only while every immutable identity
            # field remains fixed; matching bytes alone are insufficient because
            # a different promotion may republish those bytes as a new generation.
            try:
                winner = authority.get_desired(deployment_id)
            except Exception as read_exc:
                raise LearningAuthorityError(
                    "shared learned-artifact desired state is unavailable"
                ) from read_exc
            if _is_requested_release(
                winner,
                deployment_id=deployment_id,
                artifact_kind=artifact_kind,
                digest=digest,
                promotion_record_id=promotion_record_id,
                promotion_transaction_id=promotion_transaction_id,
                expected_previous_digest=expected_previous_digest,
                allow_initial_cutover=allow_initial_cutover,
            ):
                desired = winner
                break
            if _release_identity(winner) == predecessor_identity:
                continue
            raise LearningAuthorityConflict(
                "shared learned-artifact publication lost its predecessor CAS"
            ) from exc
        except LearningAuthorityError:
            raise
        except Exception as exc:
            raise LearningAuthorityError(
                "shared learned-artifact desired state is unavailable"
            ) from exc
        break
    else:
        raise LearningAuthorityError(
            "shared learned-artifact publication did not converge"
        )

    release_identity = _release_identity(desired)
    if release_identity is None:  # pragma: no cover - guarded by publication
        raise LearningAuthorityError(
            "shared learned-artifact desired state disappeared"
        )
    while desired.rollout_state == "pending":
        try:
            transitioned = authority.transition_rollout(
                deployment_id,
                "canary",
                expected_revision=desired.revision,
                actor=actor,
            )
            _require_release_identity(
                transitioned,
                release_identity,
                operation="rollout admission",
            )
            desired = transitioned
        except ArtifactReleaseConflict:
            try:
                refreshed = authority.get_desired(deployment_id)
            except Exception as exc:
                raise LearningAuthorityError(
                    "shared learned-artifact rollout state is unavailable"
                ) from exc
            _require_release_identity(
                refreshed,
                release_identity,
                operation="rollout admission",
            )
            desired = refreshed
        except LearningAuthorityError:
            raise
        except Exception as exc:
            raise LearningAuthorityError(
                "shared learned-artifact rollout admission failed"
            ) from exc
    if desired.rollout_state not in {"canary", "rolling", "ready", "rollback"}:
        raise LearningAuthorityError(
            f"shared learned-artifact rollout is {desired.rollout_state}"
        )
    return desired


def _observe_release(
    authority,
    desired,
    *,
    node_id: str,
    digest: str,
    actor: str,
):
    from .artifact_release_authority import ArtifactReleaseConflict

    for _attempt in range(_MAX_RELEASE_RETRIES):
        try:
            current = authority.get_observation(desired.deployment_id, node_id)
            if (
                current is not None
                and current.observed_generation == desired.generation
                and current.observed_digest == digest
                and current.health == "healthy"
                and not current.last_error
            ):
                return current
            return authority.report_observation(
                desired.deployment_id,
                node_id,
                observed_generation=desired.generation,
                observed_digest=digest,
                health="healthy",
                expected_revision=current.revision if current is not None else 0,
                actor=actor,
            )
        except ArtifactReleaseConflict:
            continue
        except Exception as exc:
            raise LearningAuthorityError(
                "shared learned-artifact node observation failed"
            ) from exc
    raise LearningAuthorityError(
        "shared learned-artifact node observation did not converge"
    )


def converge_learning_release(
    *,
    deployment_id: str,
    artifact_kind: str,
    digest: str,
    promotion_record_id: str,
    promotion_transaction_id: str,
    expected_previous_digest: str | None,
    actor: str,
    authority: object = None,
    node_id: object = None,
    allow_initial_cutover: bool = False,
) -> LearningRelease:
    """Publish exact desired bytes, admit rollout, and prove local readiness.

    Publishing and observing are individually retry-safe.  A retry following a
    lost database response returns the existing generation only when the same
    transaction id carries the same exact digest and promotion record.
    """
    exact_digest = _digest(digest, "artifact digest")
    previous = (
        None
        if expected_previous_digest is None
        else _digest(expected_previous_digest, "previous artifact digest")
    )
    if not isinstance(allow_initial_cutover, bool):
        raise LearningAuthorityError("allow_initial_cutover must be a boolean")
    kind = _artifact_kind(artifact_kind)
    resolved = _release_authority(authority)
    node = release_node_id(node_id)
    desired = _publish_release(
        resolved,
        deployment_id=deployment_id,
        artifact_kind=kind,
        digest=exact_digest,
        promotion_record_id=promotion_record_id,
        promotion_transaction_id=promotion_transaction_id,
        expected_previous_digest=previous,
        allow_initial_cutover=allow_initial_cutover,
        actor=actor,
    )
    if desired.artifact_kind != kind or desired.desired_digest != exact_digest:
        raise LearningAuthorityConflict(
            "shared learned-artifact desired state does not match local bytes"
        )
    _observe_release(
        resolved,
        desired,
        node_id=node,
        digest=exact_digest,
        actor=f"{actor}/node/{node}",
    )
    try:
        decision = resolved.reconciliation_decision(deployment_id, node)
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learned-artifact readiness is unavailable"
        ) from exc
    if (
        not decision.ready
        or decision.desired_generation != desired.generation
        or decision.desired_digest != exact_digest
        or decision.observed_digest != exact_digest
    ):
        raise LearningAuthorityError(
            f"shared learned-artifact is not serving-ready ({decision.action.value})"
        )
    return LearningRelease(
        deployment_id=deployment_id,
        generation=desired.generation,
        desired_digest=exact_digest,
        previous_digest=desired.previous_digest,
        rollout_state=desired.rollout_state,
        node_id=node,
        ready=True,
    )


def require_learning_release(
    *,
    deployment_id: str,
    artifact_kind: str,
    digest: str,
    actor: str,
    authority: object = None,
    node_id: object = None,
) -> LearningRelease:
    """Observe exact local bytes and require the current shared desired release."""
    exact_digest = _digest(digest, "artifact digest")
    kind = _artifact_kind(artifact_kind)
    resolved = _release_authority(authority)
    node = release_node_id(node_id)
    try:
        desired = resolved.get_desired(deployment_id)
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learned-artifact desired state is unavailable"
        ) from exc
    if desired is None:
        raise LearningAuthorityError(
            "shared learned-artifact desired state is missing"
        )
    if desired.artifact_kind != kind or desired.desired_digest != exact_digest:
        raise LearningAuthorityConflict(
            "local learned artifact is not the exact shared desired digest"
        )
    if desired.rollout_state not in {"canary", "rolling", "ready", "rollback"}:
        raise LearningAuthorityError(
            f"shared learned-artifact rollout is {desired.rollout_state}"
        )
    _observe_release(
        resolved,
        desired,
        node_id=node,
        digest=exact_digest,
        actor=f"{actor}/node/{node}",
    )
    try:
        decision = resolved.reconciliation_decision(deployment_id, node)
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learned-artifact readiness is unavailable"
        ) from exc
    if not decision.ready or decision.desired_digest != exact_digest:
        raise LearningAuthorityError(
            f"shared learned-artifact is not serving-ready ({decision.action.value})"
        )
    return LearningRelease(
        deployment_id=deployment_id,
        generation=desired.generation,
        desired_digest=exact_digest,
        previous_digest=desired.previous_digest,
        rollout_state=desired.rollout_state,
        node_id=node,
        ready=True,
    )


def retire_learning_release(
    *,
    deployment_id: str,
    actor: str,
    authority: object = None,
    node_id: object = None,
) -> LearningRelease:
    """CAS-retire one exact release and report that this node serves no artifact."""
    from .artifact_release_authority import ArtifactReleaseConflict

    resolved = _release_authority(authority)
    node = release_node_id(node_id)
    try:
        desired = resolved.get_desired(deployment_id)
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learned-artifact retirement failed"
        ) from exc
    if desired is None:
        raise LearningAuthorityError(
            "shared learned-artifact desired state is missing"
        )
    release_identity = _release_identity(desired)
    if release_identity is None:  # pragma: no cover - guarded above
        raise LearningAuthorityError(
            "shared learned-artifact desired state is missing"
        )

    for _attempt in range(_MAX_RELEASE_RETRIES):
        _require_release_identity(
            desired,
            release_identity,
            operation="retirement",
        )
        try:
            if desired.rollout_state == "retired":
                break
            next_state = (
                "failed"
                if desired.rollout_state in {"canary", "rolling", "rollback"}
                else "retired"
            )
            desired = resolved.transition_rollout(
                deployment_id,
                next_state,
                expected_revision=desired.revision,
                actor=actor,
            )
        except ArtifactReleaseConflict:
            try:
                refreshed = resolved.get_desired(deployment_id)
            except Exception as exc:
                raise LearningAuthorityError(
                    "shared learned-artifact retirement failed"
                ) from exc
            _require_release_identity(
                refreshed,
                release_identity,
                operation="retirement",
            )
            desired = refreshed
            continue
        except LearningAuthorityError:
            raise
        except Exception as exc:
            raise LearningAuthorityError(
                "shared learned-artifact retirement failed"
            ) from exc
        _require_release_identity(
            desired,
            release_identity,
            operation="retirement",
        )
    else:
        raise LearningAuthorityError(
            "shared learned-artifact retirement did not converge"
        )
    if desired.rollout_state != "retired":
        # A canary/rolling/rollback release reaches failed first; retain the
        # original identity while taking the second step to retired.
        raise LearningAuthorityError(
            "shared learned-artifact retirement did not reach a terminal state"
        )

    for _attempt in range(_MAX_RELEASE_RETRIES):
        try:
            current = resolved.get_observation(deployment_id, node)
            if (
                current is not None
                and current.observed_generation == 0
                and current.observed_digest is None
                and current.health == "offline"
            ):
                break
            resolved.report_observation(
                deployment_id,
                node,
                observed_generation=0,
                observed_digest=None,
                health="offline",
                last_error="learned artifact was retired",
                expected_revision=current.revision if current is not None else 0,
                actor=f"{actor}/node/{node}",
            )
            break
        except ArtifactReleaseConflict:
            continue
        except Exception as exc:
            raise LearningAuthorityError(
                "shared learned-artifact retirement observation failed"
            ) from exc
    else:
        raise LearningAuthorityError(
            "shared learned-artifact retirement observation did not converge"
        )
    try:
        decision = resolved.reconciliation_decision(deployment_id, node)
    except Exception as exc:
        raise LearningAuthorityError(
            "shared learned-artifact retirement readiness is unavailable"
        ) from exc
    if decision.action.value != "retire_local":
        raise LearningAuthorityError(
            "shared learned-artifact retirement returned an inconsistent decision"
        )
    return LearningRelease(
        deployment_id=deployment_id,
        generation=desired.generation,
        desired_digest=desired.desired_digest,
        previous_digest=desired.previous_digest,
        rollout_state=desired.rollout_state,
        node_id=node,
        ready=False,
    )


def _finite_timestamp(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise LearningAuthorityError(f"{label} is invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LearningAuthorityError(f"{label} is invalid") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise LearningAuthorityError(f"{label} is invalid")
    return parsed


def _validate_self_learning_document(value: object) -> dict[str, Any]:
    if value == {}:
        return {"schema": SELF_LEARNING_SCHEMA, "generation": 0, "entries": []}
    if not isinstance(value, dict) or set(value) != {
        "schema", "generation", "entries",
    }:
        raise LearningAuthorityError("shared self-learning state is malformed")
    generation = value.get("generation")
    entries = value.get("entries")
    if (
        value.get("schema") != SELF_LEARNING_SCHEMA
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or not isinstance(entries, list)
        or len(entries) > _MAX_SELF_LEARNING_ENTRIES
    ):
        raise LearningAuthorityError("shared self-learning state is malformed")
    expected_fields = {
        "id", "intent_sha256", "ts", "need", "kind", "name", "source", "outcome",
    }
    seen: set[str] = set()
    canonical_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != expected_fields:
            raise LearningAuthorityError("shared self-learning entry is malformed")
        entry_id = entry.get("id")
        intent = entry.get("intent_sha256")
        if (
            not isinstance(entry_id, str)
            or not _SHA256_RE.fullmatch(entry_id)
            or entry_id in seen
            or not isinstance(intent, str)
            or not _SHA256_RE.fullmatch(intent)
        ):
            raise LearningAuthorityError("shared self-learning entry is malformed")
        _finite_timestamp(entry.get("ts"), "self-learning timestamp")
        for field in ("need", "kind", "name", "source", "outcome"):
            item = entry.get(field)
            if not isinstance(item, str):
                raise LearningAuthorityError(
                    "shared self-learning entry is malformed"
                )
        logical = {
            field: entry[field]
            for field in ("need", "kind", "name", "source", "outcome")
        }
        if not _sha256(_canonical(logical)) == intent:
            raise LearningAuthorityError(
                "shared self-learning entry intent digest is invalid"
            )
        seen.add(entry_id)
        canonical_entries.append(dict(entry))
    if generation != len(canonical_entries):
        raise LearningAuthorityError(
            "shared self-learning generation is inconsistent"
        )
    if len(_canonical(value)) > _MAX_SELF_LEARNING_DOCUMENT_BYTES:
        raise LearningAuthorityError(
            "shared self-learning state exceeds its size limit"
        )
    return {
        "schema": SELF_LEARNING_SCHEMA,
        "generation": generation,
        "entries": canonical_entries,
    }


def record_self_learning_entry(
    logical: Mapping[str, str],
    *,
    observation_id: str,
    actor: str = "self-learning",
    store: object = None,
) -> dict[str, Any]:
    """CAS-append one idempotent shared learned-capability observation."""
    if (
        not isinstance(observation_id, str)
        or not observation_id
        or len(observation_id) > _MAX_OBSERVATION_ID
        or any(ord(char) < 32 or ord(char) == 127 for char in observation_id)
    ):
        raise LearningAuthorityError("self-learning observation id is invalid")
    expected = {"need", "kind", "name", "source", "outcome"}
    if set(logical) != expected or not all(
        isinstance(logical[field], str) for field in expected
    ):
        raise LearningAuthorityError("self-learning entry intent is malformed")
    intent = {field: logical[field] for field in sorted(expected)}
    intent_digest = _sha256(_canonical(intent))
    entry_id = _sha256(observation_id.encode("utf-8"))
    selected = store or _SELF_LEARNING

    def mutate(document: dict[str, Any], now: float) -> dict[str, Any]:
        current = _validate_self_learning_document(document)
        for entry in current["entries"]:
            if entry["id"] != entry_id:
                continue
            if entry["intent_sha256"] != intent_digest:
                raise LearningAuthorityConflict(
                    "self-learning observation id was reused with different intent"
                )
            document.clear()
            document.update(current)
            return dict(entry)
        if len(current["entries"]) >= _MAX_SELF_LEARNING_ENTRIES:
            raise LearningAuthorityError(
                "shared self-learning state reached its entry limit"
            )
        entry = {
            "id": entry_id,
            "intent_sha256": intent_digest,
            "ts": _finite_timestamp(now, "authority timestamp"),
            **intent,
        }
        current["entries"].append(entry)
        current["generation"] += 1
        if len(_canonical(current)) > _MAX_SELF_LEARNING_DOCUMENT_BYTES:
            raise LearningAuthorityError(
                "shared self-learning state exceeds its size limit"
            )
        document.clear()
        document.update(current)
        return dict(entry)

    try:
        return selected.mutate_with_authoritative_time(
            mutate,
            action="record_learning_admission",
            actor=actor,
        )
    except LearningAuthorityError:
        raise
    except Exception as exc:
        raise LearningAuthorityError(
            "shared self-learning admission state is unavailable"
        ) from exc


def self_learning_entries(*, store: object = None) -> list[dict[str, Any]]:
    """Return a validated defensive copy of the shared learning ledger."""
    selected = store or _SELF_LEARNING
    try:
        document = selected.read()
    except Exception as exc:
        raise LearningAuthorityError(
            "shared self-learning admission state is unavailable"
        ) from exc
    validated = _validate_self_learning_document(document)
    return [dict(entry) for entry in validated["entries"]]


def _revision_payload(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "identity", "sha256", "version",
    }:
        raise LearningAuthorityError(f"confirmation {label} revision is malformed")
    identity = value.get("identity")
    version = value.get("version")
    digest = value.get("sha256")
    if (
        not isinstance(identity, str)
        or not identity
        or len(identity) > 4096
        or any(ord(char) < 32 or ord(char) == 127 for char in identity)
        or not isinstance(version, str)
        or not version
        or len(version) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in version)
    ):
        raise LearningAuthorityError(f"confirmation {label} revision is malformed")
    return {
        "identity": identity,
        "sha256": _digest(digest, f"confirmation {label} digest"),
        "version": version,
    }


def _parse_confirmation_claim(
    raw: object,
    *,
    cohort_sha256: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw).difference(
        _CONFIRMATION_FIELDS | _STORE_FIELDS
    ):
        raise LearningAuthorityError("verifier confirmation claim is malformed")
    cohort = _digest(raw.get("cohort_sha256"), "confirmation cohort")
    before = _revision_payload(raw.get("before"), "before")
    challenger = _revision_payload(raw.get("challenger"), "challenger")
    if before["identity"] != challenger["identity"]:
        raise LearningAuthorityError(
            "verifier confirmation claim crosses artifact identities"
        )
    logical = {
        "cohort_sha256": cohort,
        "before": before,
        "challenger": challenger,
    }
    intent = _sha256(_canonical(logical))
    expected_id = "VCC-" + _sha256(cohort.encode("ascii"))[:60]
    if (
        raw.get("id") != expected_id
        or raw.get("schema") != CONFIRMATION_SCHEMA
        or raw.get("record_kind") != "claim"
        or raw.get("status") != "consumed"
        or raw.get("intent_sha256") != intent
        or cohort != cohort_sha256
    ):
        raise LearningAuthorityError(
            "verifier confirmation claim binding is invalid"
        )
    _finite_timestamp(raw.get("consumed_at"), "confirmation consumption time")
    return dict(raw)


def claim_verifier_confirmation(
    *,
    cohort_sha256: str,
    before: Mapping[str, object],
    challenger: Mapping[str, object],
    actor: str = "verifier-evaluation",
    store: object = None,
) -> dict[str, Any]:
    """Consume one cohort once, with idempotent lost-response recovery."""
    cohort = _digest(cohort_sha256, "confirmation cohort")
    before_revision = _revision_payload(before, "before")
    challenger_revision = _revision_payload(challenger, "challenger")
    if before_revision["identity"] != challenger_revision["identity"]:
        raise LearningAuthorityError(
            "confirmation artifact identity does not match deployment"
        )
    logical = {
        "cohort_sha256": cohort,
        "before": before_revision,
        "challenger": challenger_revision,
    }
    intent = _sha256(_canonical(logical))
    record_id = "VCC-" + _sha256(cohort.encode("ascii"))[:60]
    selected = store or _CONFIRMATION_CLAIMS
    try:
        now = _finite_timestamp(
            selected.authoritative_time(), "confirmation authority time",
        )
        raw = {
            "id": record_id,
            "schema": CONFIRMATION_SCHEMA,
            "record_kind": "claim",
            **logical,
            "intent_sha256": intent,
            "consumed_at": now,
            "status": "consumed",
        }
        try:
            saved = selected.create(
                raw,
                action="consume_confirmation",
                actor=actor,
            )
        except RecordConflict:
            saved = selected.get(record_id)
            existing = _parse_confirmation_claim(
                saved, cohort_sha256=cohort,
            )
            if existing.get("intent_sha256") != intent:
                raise LearningAuthorityConflict(
                    "confirmation cohort was already consumed by another challenger"
                ) from None
            return existing
        return _parse_confirmation_claim(saved, cohort_sha256=cohort)
    except LearningAuthorityError:
        raise
    except Exception as exc:
        raise LearningAuthorityError(
            "shared verifier confirmation authority is unavailable"
        ) from exc


__all__ = [
    "CONFIRMATION_SCHEMA",
    "LEARNING_RELEASE_NODE_ENV",
    "SELF_LEARNING_SCHEMA",
    "LearningAuthorityConflict",
    "LearningAuthorityError",
    "LearningRelease",
    "claim_verifier_confirmation",
    "converge_learning_release",
    "learning_deployment_id",
    "promotion_authority_provenance",
    "record_self_learning_entry",
    "release_intent_ids",
    "release_node_id",
    "retire_learning_release",
    "require_learning_release",
    "self_learning_entries",
    "shared_learning_authority_selected",
]
