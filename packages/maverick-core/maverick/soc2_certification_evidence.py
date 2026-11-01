"""Digest-only foundation for SOC 2 evidence collection.

This module creates bounded technical evidence for three operational areas:

* access-review state (SOC 2 CC6.2);
* a local Git change-management snapshot (SOC 2 CC8.1); and
* audit-log retention posture (SOC 2 CC7.2).

It deliberately does not certify any control, contact a provider, inspect an
identity tenant, resolve credentials, or enable a scheduler. Inputs use only
fixed status values, counts, and SHA-256 references. Raw review records,
principal names, Git paths/branches/commits, audit rows, and probe errors never
enter an evidence bundle or a TRC EvidencePage.

Soc2CertificationEvidenceCollector implements the closed continuous-evidence
collector contract. Its source reader is injected, so deployment code decides
when an approved local source may be read. The separate
select_due_soc2_certification_runs function is pure: a durable scheduler must
persist its selected run and proposed schedule advance under its own CAS or
transaction before invoking the collector.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .suite_audit_capture import SuiteAuditCapture
from .trc_evidence_collectors import (
    CollectionRequest,
    ControlEvidence,
    EvidenceCollectorValidationError,
    EvidencePage,
    validate_page,
    validate_request,
)

SOC2_CERTIFICATION_COLLECTOR_SCHEMA = (
    "lightwork.soc2-certification-evidence-collector.v1"
)
SOC2_CERTIFICATION_BUNDLE_SCHEMA = "lightwork.soc2-certification-evidence-bundle.v1"
SOC2_CERTIFICATION_SCHEDULE_SCHEMA = (
    "lightwork.soc2-certification-evidence-schedule.v1"
)
SOC2_CERTIFICATION_RUN_SCHEMA = "lightwork.soc2-certification-evidence-run.v1"
SOC2_CERTIFICATION_COLLECTOR_ID = "local_soc2_certification_v1"
SOC2_CERTIFICATION_NOTICE = (
    "This is digest-only technical evidence for audit preparation. It does not "
    "establish SOC 2 compliance, a certification outcome, or an auditor opinion."
)

NETWORK_IO = False
LIVE_AUDIT_ACCEPTANCE = False
CERTIFICATION_ASSERTED = False
RAW_SOURCE_CONTENT_PERSISTED = False

SOC2_ACCESS_REVIEW_CONTROL = "SOC2.CC6.2"
SOC2_CHANGE_MANAGEMENT_CONTROL = "SOC2.CC8.1"
SOC2_AUDIT_RETENTION_CONTROL = "SOC2.CC7.2"

_CONTROL_BY_SOURCE = {
    "access_review": SOC2_ACCESS_REVIEW_CONTROL,
    "change_management": SOC2_CHANGE_MANAGEMENT_CONTROL,
    "audit_retention": SOC2_AUDIT_RETENTION_CONTROL,
}
_SOURCE_IDS = tuple(sorted(_CONTROL_BY_SOURCE))
_CONTROL_REFS = tuple(sorted(_CONTROL_BY_SOURCE.values()))
_ID_RE = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_STATUSES = frozenset({"observed", "failed", "unknown"})
_AUDIT_CHAIN_STATUSES = frozenset(
    {"ok", "broken", "unsigned", "empty", "no_crypto", "unknown"}
)
_SCHEDULE_STATES = frozenset({"active", "paused"})
_RUN_STATES = frozenset(
    {"queued", "leased", "retry_wait", "completed", "dead_letter", "cancelled"}
)
_LIVE_RUN_STATES = frozenset({"queued", "leased", "retry_wait"})
_DUE_MODES = frozenset({"new_due", "queued_replay", "retry_due", "lease_recovered"})
_LIMITATIONS = frozenset(
    {
        "access_review_approval_unverified",
        "access_review_population_partial",
        "access_review_source_unavailable",
        "audit_anchor_coverage_unverified",
        "audit_chain_unverified",
        "audit_retention_marker_unverified",
        "audit_retention_policy_unconfigured",
        "git_change_approval_unverified",
        "git_history_scope_unverified",
        "git_repository_unavailable",
        "source_capture_partial",
    }
)
_MAX_INTERVAL_SECONDS = 366 * 24 * 60 * 60
_MIN_INTERVAL_SECONDS = 60
_MAX_ATTEMPTS = 8
_MAX_LIMIT = 500


class Soc2CertificationEvidenceError(RuntimeError):
    """Base error for bounded SOC 2 evidence preparation."""


class Soc2CertificationEvidenceValidationError(
    Soc2CertificationEvidenceError,
    ValueError,
):
    """A source, schedule, or retained digest-only value is invalid."""


class Soc2CertificationScheduleError(Soc2CertificationEvidenceError):
    """A caller supplied conflicting schedule/run state."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise Soc2CertificationEvidenceValidationError(
            "evidence value must be finite canonical JSON"
        ) from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise Soc2CertificationEvidenceValidationError(f"{label} is invalid")
    return value


def _optional_digest(value: object, label: str) -> str | None:
    return None if value is None else _digest(value, label)


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise Soc2CertificationEvidenceValidationError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) < 100_000_000_000.0
    ):
        raise Soc2CertificationEvidenceValidationError(f"{label} is invalid")
    return float(value)


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise Soc2CertificationEvidenceValidationError(f"{label} is outside its bound")
    return value


def _optional_count(value: object, label: str) -> int | None:
    return None if value is None else _integer(value, label, 0, 10_000_000)


def _optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise Soc2CertificationEvidenceValidationError(f"{label} is invalid")
    return value


def _limitations(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise Soc2CertificationEvidenceValidationError("limitations must be a tuple")
    if tuple(sorted(set(value))) != value or any(item not in _LIMITATIONS for item in value):
        raise Soc2CertificationEvidenceValidationError("limitations are invalid")
    return value


def _source_id(value: object) -> str:
    if value not in _SOURCE_IDS:
        raise Soc2CertificationEvidenceValidationError("source_id is invalid")
    return str(value)


def _next_due(scheduled_for: float, interval_seconds: int, now: float) -> float:
    elapsed = max(0.0, now - scheduled_for)
    return scheduled_for + (int(elapsed // interval_seconds) + 1) * interval_seconds


def suite_audit_capture_reference(capture: SuiteAuditCapture) -> str:
    """Return a SHA-256 reference without exposing tenant or source records."""
    if not isinstance(capture, SuiteAuditCapture):
        raise Soc2CertificationEvidenceValidationError("suite audit capture is invalid")
    return _sha256(
        {
            "schema": "lightwork.soc2-certification-suite-capture-ref.v1",
            "capture": capture.public_metadata(),
        }
    )


@dataclass(frozen=True, slots=True)
class AccessReviewState:
    """Closed access-review state supplied by a Lightwork-owned source."""

    source_revision_sha256: str
    review_completed: bool | None
    reviewed_principal_count: int | None
    reviewed_principals_sha256: str | None
    approval_sha256: str | None
    coverage_complete: bool
    limitations: tuple[str, ...]
    suite_capture_sha256: str | None = None

    def __post_init__(self) -> None:
        source = _digest(self.source_revision_sha256, "access review source revision")
        completed = _optional_bool(self.review_completed, "access review completion")
        count = _optional_count(
            self.reviewed_principal_count,
            "access review principal count",
        )
        principals = _optional_digest(
            self.reviewed_principals_sha256,
            "access review principal reference",
        )
        approval = _optional_digest(self.approval_sha256, "access review approval reference")
        if not isinstance(self.coverage_complete, bool):
            raise Soc2CertificationEvidenceValidationError(
                "access review coverage_complete is invalid"
            )
        limits = _limitations(self.limitations)
        capture = _optional_digest(self.suite_capture_sha256, "suite capture reference")
        if self.coverage_complete and (
            completed is not True or count is None or principals is None
        ):
            raise Soc2CertificationEvidenceValidationError(
                "complete access review requires closed review coverage"
            )
        object.__setattr__(self, "source_revision_sha256", source)
        object.__setattr__(self, "review_completed", completed)
        object.__setattr__(self, "reviewed_principal_count", count)
        object.__setattr__(self, "reviewed_principals_sha256", principals)
        object.__setattr__(self, "approval_sha256", approval)
        object.__setattr__(self, "limitations", limits)
        object.__setattr__(self, "suite_capture_sha256", capture)


@dataclass(frozen=True, slots=True)
class GitChangeManagementSnapshot:
    """A local Git state snapshot whose raw repository data remains ephemeral."""

    source_revision_sha256: str
    available: bool
    repository_sha256: str
    head_commit_sha256: str | None
    worktree_clean: bool | None
    history_complete: bool
    coverage_complete: bool
    limitations: tuple[str, ...]
    suite_capture_sha256: str | None = None

    def __post_init__(self) -> None:
        source = _digest(self.source_revision_sha256, "Git source revision")
        if not isinstance(self.available, bool):
            raise Soc2CertificationEvidenceValidationError("Git availability is invalid")
        repository = _digest(self.repository_sha256, "Git repository reference")
        head = _optional_digest(self.head_commit_sha256, "Git commit reference")
        clean = _optional_bool(self.worktree_clean, "Git worktree state")
        if not isinstance(self.history_complete, bool) or not isinstance(
            self.coverage_complete,
            bool,
        ):
            raise Soc2CertificationEvidenceValidationError("Git coverage flags are invalid")
        limits = _limitations(self.limitations)
        capture = _optional_digest(self.suite_capture_sha256, "suite capture reference")
        if self.available and (head is None or clean is None):
            raise Soc2CertificationEvidenceValidationError(
                "available Git snapshot requires a closed head and worktree state"
            )
        if self.coverage_complete and (
            not self.available or not self.history_complete or limits
        ):
            raise Soc2CertificationEvidenceValidationError(
                "complete Git coverage cannot carry unresolved limitations"
            )
        object.__setattr__(self, "source_revision_sha256", source)
        object.__setattr__(self, "repository_sha256", repository)
        object.__setattr__(self, "head_commit_sha256", head)
        object.__setattr__(self, "worktree_clean", clean)
        object.__setattr__(self, "limitations", limits)
        object.__setattr__(self, "suite_capture_sha256", capture)


@dataclass(frozen=True, slots=True)
class AuditLogRetentionPosture:
    """Closed audit-chain and retention-policy posture, never audit rows."""

    source_revision_sha256: str
    audit_chain_status: str
    policy_configured: bool
    audit_days: int | None
    anchors_checked: bool | None
    retention_markers_verified: bool | None
    coverage_complete: bool
    limitations: tuple[str, ...]
    suite_capture_sha256: str | None = None

    def __post_init__(self) -> None:
        source = _digest(self.source_revision_sha256, "audit retention source revision")
        if self.audit_chain_status not in _AUDIT_CHAIN_STATUSES:
            raise Soc2CertificationEvidenceValidationError("audit chain status is invalid")
        if not isinstance(self.policy_configured, bool):
            raise Soc2CertificationEvidenceValidationError(
                "audit retention policy state is invalid"
            )
        days = _optional_count(self.audit_days, "audit retention days")
        if days is not None and days > 36_600:
            raise Soc2CertificationEvidenceValidationError(
                "audit retention days is outside its bound"
            )
        anchors = _optional_bool(self.anchors_checked, "audit anchor state")
        markers = _optional_bool(
            self.retention_markers_verified,
            "audit retention marker state",
        )
        if not isinstance(self.coverage_complete, bool):
            raise Soc2CertificationEvidenceValidationError(
                "audit retention coverage_complete is invalid"
            )
        limits = _limitations(self.limitations)
        capture = _optional_digest(self.suite_capture_sha256, "suite capture reference")
        if self.policy_configured != (days is not None and days > 0):
            raise Soc2CertificationEvidenceValidationError(
                "audit retention policy and days do not agree"
            )
        if self.coverage_complete and (
            self.audit_chain_status != "ok"
            or not self.policy_configured
            or anchors is not True
            or markers is not True
            or limits
        ):
            raise Soc2CertificationEvidenceValidationError(
                "complete audit retention coverage is unsupported by its posture"
            )
        object.__setattr__(self, "source_revision_sha256", source)
        object.__setattr__(self, "audit_days", days)
        object.__setattr__(self, "anchors_checked", anchors)
        object.__setattr__(self, "retention_markers_verified", markers)
        object.__setattr__(self, "limitations", limits)
        object.__setattr__(self, "suite_capture_sha256", capture)


@dataclass(frozen=True, slots=True)
class Soc2EvidenceCoverage:
    """Explicit source coverage and limitation metadata for one control."""

    source_id: str
    available: bool
    coverage_complete: bool
    source_revision_sha256: str
    evidence_ref_sha256: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        source = _source_id(self.source_id)
        if not isinstance(self.available, bool) or not isinstance(
            self.coverage_complete,
            bool,
        ):
            raise Soc2CertificationEvidenceValidationError("coverage flags are invalid")
        revision = _digest(self.source_revision_sha256, "coverage source revision")
        evidence = _digest(self.evidence_ref_sha256, "coverage evidence reference")
        limits = _limitations(self.limitations)
        if self.coverage_complete and limits:
            raise Soc2CertificationEvidenceValidationError(
                "complete coverage cannot carry limitations"
            )
        object.__setattr__(self, "source_id", source)
        object.__setattr__(self, "source_revision_sha256", revision)
        object.__setattr__(self, "evidence_ref_sha256", evidence)
        object.__setattr__(self, "limitations", limits)


@dataclass(frozen=True, slots=True)
class Soc2CertificationEvidenceBundle:
    """Self-validating, closed metadata usable by a TRC collector."""

    controls: tuple[ControlEvidence, ...]
    coverage: tuple[Soc2EvidenceCoverage, ...]
    limitations: tuple[str, ...]
    source_revision_sha256: str
    evidence_sha256: str
    notice: str = SOC2_CERTIFICATION_NOTICE
    legal_certification: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.controls, tuple)
            or any(not isinstance(item, ControlEvidence) for item in self.controls)
            or tuple(item.control_ref for item in self.controls) != _CONTROL_REFS
        ):
            raise Soc2CertificationEvidenceValidationError(
                "SOC 2 certification controls are invalid"
            )
        for item in self.controls:
            if item.status not in _STATUSES or not _REASON_RE.fullmatch(item.reason_code):
                raise Soc2CertificationEvidenceValidationError(
                    "SOC 2 certification observation is invalid"
                )
            _digest(item.evidence_sha256, "control evidence reference")
            _digest(item.source_revision_sha256, "control source revision")
        if (
            not isinstance(self.coverage, tuple)
            or any(not isinstance(item, Soc2EvidenceCoverage) for item in self.coverage)
            or tuple(item.source_id for item in self.coverage) != _SOURCE_IDS
        ):
            raise Soc2CertificationEvidenceValidationError("SOC 2 evidence coverage is invalid")
        limits = _limitations(self.limitations)
        aggregate = _digest(self.source_revision_sha256, "bundle source revision")
        supplied = _digest(self.evidence_sha256, "bundle evidence reference")
        if self.notice != SOC2_CERTIFICATION_NOTICE or self.legal_certification is not False:
            raise Soc2CertificationEvidenceValidationError(
                "SOC 2 certification notice is invalid"
            )
        material = {
            "schema": SOC2_CERTIFICATION_BUNDLE_SCHEMA,
            "collector_id": SOC2_CERTIFICATION_COLLECTOR_ID,
            "controls": [asdict(item) for item in self.controls],
            "coverage": [asdict(item) for item in self.coverage],
            "limitations": list(limits),
            "source_revision_sha256": aggregate,
            "notice": SOC2_CERTIFICATION_NOTICE,
            "legal_certification": False,
        }
        if supplied != _sha256(material):
            raise Soc2CertificationEvidenceValidationError(
                "bundle evidence reference does not match its closed metadata"
            )
        object.__setattr__(self, "limitations", limits)
        object.__setattr__(self, "source_revision_sha256", aggregate)
        object.__setattr__(self, "evidence_sha256", supplied)

    def public_metadata(self) -> dict[str, object]:
        """Return only non-content evidence metadata for evidence packages."""
        return {
            "schema": SOC2_CERTIFICATION_BUNDLE_SCHEMA,
            "collector_id": SOC2_CERTIFICATION_COLLECTOR_ID,
            "controls": [asdict(item) for item in self.controls],
            "coverage": [asdict(item) for item in self.coverage],
            "limitations": list(self.limitations),
            "source_revision_sha256": self.source_revision_sha256,
            "evidence_sha256": self.evidence_sha256,
            "notice": SOC2_CERTIFICATION_NOTICE,
            "legal_certification": False,
        }


def _access_review_observation(
    state: AccessReviewState,
) -> tuple[ControlEvidence, Soc2EvidenceCoverage]:
    if state.review_completed is True:
        status = "observed"
        reason = (
            "access_review_complete"
            if state.coverage_complete
            else "access_review_partial"
        )
    elif state.review_completed is False:
        status = "failed"
        reason = "access_review_incomplete"
    else:
        status = "unknown"
        reason = "access_review_unavailable"
    material = {
        "schema": "lightwork.soc2-access-review-evidence.v1",
        "source_revision_sha256": state.source_revision_sha256,
        "review_completed": state.review_completed,
        "reviewed_principal_count": state.reviewed_principal_count,
        "reviewed_principals_sha256": state.reviewed_principals_sha256,
        "approval_sha256": state.approval_sha256,
        "coverage_complete": state.coverage_complete,
        "limitations": list(state.limitations),
        "suite_capture_sha256": state.suite_capture_sha256,
    }
    evidence = _sha256(material)
    return (
        ControlEvidence(
            SOC2_ACCESS_REVIEW_CONTROL,
            status,
            reason,
            evidence,
            state.source_revision_sha256,
        ),
        Soc2EvidenceCoverage(
            "access_review",
            state.review_completed is not None,
            state.coverage_complete,
            state.source_revision_sha256,
            evidence,
            state.limitations,
        ),
    )


def _change_management_observation(
    state: GitChangeManagementSnapshot,
) -> tuple[ControlEvidence, Soc2EvidenceCoverage]:
    if not state.available:
        status = "unknown"
        reason = "git_snapshot_unavailable"
    elif state.worktree_clean is False:
        status = "failed"
        reason = "git_worktree_dirty"
    else:
        status = "observed"
        reason = "git_snapshot_captured"
    material = {
        "schema": "lightwork.soc2-git-change-management-evidence.v1",
        "source_revision_sha256": state.source_revision_sha256,
        "available": state.available,
        "repository_sha256": state.repository_sha256,
        "head_commit_sha256": state.head_commit_sha256,
        "worktree_clean": state.worktree_clean,
        "history_complete": state.history_complete,
        "coverage_complete": state.coverage_complete,
        "limitations": list(state.limitations),
        "suite_capture_sha256": state.suite_capture_sha256,
    }
    evidence = _sha256(material)
    return (
        ControlEvidence(
            SOC2_CHANGE_MANAGEMENT_CONTROL,
            status,
            reason,
            evidence,
            state.source_revision_sha256,
        ),
        Soc2EvidenceCoverage(
            "change_management",
            state.available,
            state.coverage_complete,
            state.source_revision_sha256,
            evidence,
            state.limitations,
        ),
    )


def _audit_retention_observation(
    state: AuditLogRetentionPosture,
) -> tuple[ControlEvidence, Soc2EvidenceCoverage]:
    if state.audit_chain_status == "unknown":
        status = "unknown"
        reason = "audit_retention_unknown"
    elif state.audit_chain_status == "broken" or not state.policy_configured:
        status = "failed"
        reason = "audit_retention_failed"
    elif state.audit_chain_status == "ok":
        status = "observed"
        reason = "audit_retention_observed"
    else:
        status = "unknown"
        reason = "audit_retention_unknown"
    material = {
        "schema": "lightwork.soc2-audit-retention-evidence.v1",
        "source_revision_sha256": state.source_revision_sha256,
        "audit_chain_status": state.audit_chain_status,
        "policy_configured": state.policy_configured,
        "audit_days": state.audit_days,
        "anchors_checked": state.anchors_checked,
        "retention_markers_verified": state.retention_markers_verified,
        "coverage_complete": state.coverage_complete,
        "limitations": list(state.limitations),
        "suite_capture_sha256": state.suite_capture_sha256,
    }
    evidence = _sha256(material)
    return (
        ControlEvidence(
            SOC2_AUDIT_RETENTION_CONTROL,
            status,
            reason,
            evidence,
            state.source_revision_sha256,
        ),
        Soc2EvidenceCoverage(
            "audit_retention",
            state.audit_chain_status != "unknown",
            state.coverage_complete,
            state.source_revision_sha256,
            evidence,
            state.limitations,
        ),
    )


def collect_soc2_certification_evidence(
    *,
    access_review: AccessReviewState,
    change_management: GitChangeManagementSnapshot,
    audit_retention: AuditLogRetentionPosture,
) -> Soc2CertificationEvidenceBundle:
    """Build a stable, digest-only technical bundle from closed source states."""
    if not isinstance(access_review, AccessReviewState):
        raise Soc2CertificationEvidenceValidationError("access review state is invalid")
    if not isinstance(change_management, GitChangeManagementSnapshot):
        raise Soc2CertificationEvidenceValidationError("Git snapshot is invalid")
    if not isinstance(audit_retention, AuditLogRetentionPosture):
        raise Soc2CertificationEvidenceValidationError("audit retention posture is invalid")
    access, access_coverage = _access_review_observation(access_review)
    change, change_coverage = _change_management_observation(change_management)
    audit, audit_coverage = _audit_retention_observation(audit_retention)
    controls = tuple(sorted((access, change, audit), key=lambda item: item.control_ref))
    coverage = tuple(
        sorted(
            (access_coverage, change_coverage, audit_coverage),
            key=lambda item: item.source_id,
        )
    )
    limits = tuple(sorted({item for row in coverage for item in row.limitations}))
    aggregate = _sha256(
        {
            "schema": "lightwork.soc2-certification-source-revision.v1",
            "sources": [
                {
                    "source_id": row.source_id,
                    "source_revision_sha256": row.source_revision_sha256,
                    "evidence_ref_sha256": row.evidence_ref_sha256,
                }
                for row in coverage
            ],
        }
    )
    material = {
        "schema": SOC2_CERTIFICATION_BUNDLE_SCHEMA,
        "collector_id": SOC2_CERTIFICATION_COLLECTOR_ID,
        "controls": [asdict(item) for item in controls],
        "coverage": [asdict(item) for item in coverage],
        "limitations": list(limits),
        "source_revision_sha256": aggregate,
        "notice": SOC2_CERTIFICATION_NOTICE,
        "legal_certification": False,
    }
    return Soc2CertificationEvidenceBundle(
        controls=controls,
        coverage=coverage,
        limitations=limits,
        source_revision_sha256=aggregate,
        evidence_sha256=_sha256(material),
    )


def capture_git_change_management_snapshot(
    worktree: str | Path,
    *,
    state_reader: Callable[[str], Mapping[str, object] | None] | None = None,
    suite_capture_sha256: str | None = None,
) -> GitChangeManagementSnapshot:
    """Capture only hashes of a local Git state; never repository content.

    The default reader is maverick.trace_pin.workspace_git_state, which uses
    argv-list subprocess calls and does not fetch or mutate a repository. The
    resulting snapshot intentionally remains partial: it cannot establish
    pull-request review, change approval, or complete historical coverage.
    """
    try:
        root = Path(worktree).resolve()
    except (OSError, TypeError, ValueError):
        root = Path(".").resolve()
    repository = _sha256(
        {
            "schema": "lightwork.soc2-git-repository-ref.v1",
            "worktree": str(root),
        }
    )
    reader = state_reader
    if reader is None:
        from .trace_pin import workspace_git_state

        reader = workspace_git_state
    try:
        raw = reader(str(root))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raw = None
    if not isinstance(raw, Mapping):
        source = _sha256(
            {
                "schema": "lightwork.soc2-git-snapshot.v1",
                "repository_sha256": repository,
                "available": False,
            }
        )
        return GitChangeManagementSnapshot(
            source_revision_sha256=source,
            available=False,
            repository_sha256=repository,
            head_commit_sha256=None,
            worktree_clean=None,
            history_complete=False,
            coverage_complete=False,
            limitations=("git_repository_unavailable",),
            suite_capture_sha256=suite_capture_sha256,
        )
    commit = raw.get("commit")
    branch = raw.get("branch")
    dirty = raw.get("dirty")
    if (
        not isinstance(commit, str)
        or not commit
        or len(commit) > 256
        or any(ord(char) < 32 for char in commit)
        or not isinstance(branch, str)
        or len(branch) > 256
        or any(ord(char) < 32 for char in branch)
        or not isinstance(dirty, bool)
    ):
        return capture_git_change_management_snapshot(
            root,
            state_reader=lambda _cwd: None,
            suite_capture_sha256=suite_capture_sha256,
        )
    head = _sha256({"schema": "lightwork.soc2-git-head-ref.v1", "commit": commit})
    source = _sha256(
        {
            "schema": "lightwork.soc2-git-snapshot.v1",
            "repository_sha256": repository,
            "head_commit_sha256": head,
            "branch_sha256": _sha256(
                {"schema": "lightwork.soc2-git-branch-ref.v1", "branch": branch}
            ),
            "worktree_clean": not dirty,
        }
    )
    return GitChangeManagementSnapshot(
        source_revision_sha256=source,
        available=True,
        repository_sha256=repository,
        head_commit_sha256=head,
        worktree_clean=not dirty,
        history_complete=False,
        coverage_complete=False,
        limitations=(
            "git_change_approval_unverified",
            "git_history_scope_unverified",
        ),
        suite_capture_sha256=suite_capture_sha256,
    )


def audit_retention_posture_from_soc2_snapshot(
    *,
    soc2_snapshot: Mapping[str, object],
    retention_config: Mapping[str, object] | None,
    suite_capture_sha256: str | None = None,
) -> AuditLogRetentionPosture:
    """Normalize safe SOC 2 audit posture without retaining probe details.

    retention_config must be the already-selected retention table. This helper
    does not call load_config or invoke an audit probe itself; callers choose
    when those local reads are permitted.
    """
    snapshot = soc2_snapshot if isinstance(soc2_snapshot, Mapping) else {}
    audit = snapshot.get("audit_log")
    audit = audit if isinstance(audit, Mapping) else {}
    raw_status = audit.get("status")
    status = raw_status if raw_status in _AUDIT_CHAIN_STATUSES else "unknown"
    raw_days = (
        retention_config.get("audit_days")
        if isinstance(retention_config, Mapping)
        else None
    )
    days = (
        raw_days
        if isinstance(raw_days, int)
        and not isinstance(raw_days, bool)
        and 1 <= raw_days <= 36_600
        else None
    )
    policy_configured = days is not None
    anchors = audit.get("anchors_checked")
    anchors = anchors if isinstance(anchors, bool) else None
    # Existing SOC 2 probes validate integrity/anchor status, but do not claim
    # every policy purge marker had an independent review.
    markers_verified = None
    limits: set[str] = set()
    if not policy_configured:
        limits.add("audit_retention_policy_unconfigured")
    if status != "ok":
        limits.add("audit_chain_unverified")
    if anchors is not True:
        limits.add("audit_anchor_coverage_unverified")
    limits.add("audit_retention_marker_unverified")
    normalized_limits = tuple(sorted(limits))
    source = _sha256(
        {
            "schema": "lightwork.soc2-audit-retention-posture.v1",
            "audit_chain_status": status,
            "policy_configured": policy_configured,
            "audit_days": days,
            "anchors_checked": anchors,
            "retention_markers_verified": markers_verified,
            "limitations": list(normalized_limits),
            "suite_capture_sha256": suite_capture_sha256,
        }
    )
    return AuditLogRetentionPosture(
        source_revision_sha256=source,
        audit_chain_status=status,
        policy_configured=policy_configured,
        audit_days=days,
        anchors_checked=anchors,
        retention_markers_verified=markers_verified,
        coverage_complete=False,
        limitations=normalized_limits,
        suite_capture_sha256=suite_capture_sha256,
    )


def _unavailable_bundle() -> Soc2CertificationEvidenceBundle:
    """Use fixed opaque failures when an injected source reader raises."""
    return collect_soc2_certification_evidence(
        access_review=AccessReviewState(
            source_revision_sha256=_sha256(
                {"schema": "lightwork.soc2-access-review-unavailable.v1"}
            ),
            review_completed=None,
            reviewed_principal_count=None,
            reviewed_principals_sha256=None,
            approval_sha256=None,
            coverage_complete=False,
            limitations=("access_review_source_unavailable",),
        ),
        change_management=GitChangeManagementSnapshot(
            source_revision_sha256=_sha256(
                {"schema": "lightwork.soc2-git-unavailable.v1"}
            ),
            available=False,
            repository_sha256=_sha256(
                {"schema": "lightwork.soc2-git-repository-unavailable.v1"}
            ),
            head_commit_sha256=None,
            worktree_clean=None,
            history_complete=False,
            coverage_complete=False,
            limitations=("git_repository_unavailable",),
        ),
        audit_retention=AuditLogRetentionPosture(
            source_revision_sha256=_sha256(
                {"schema": "lightwork.soc2-audit-retention-unavailable.v1"}
            ),
            audit_chain_status="unknown",
            policy_configured=False,
            audit_days=None,
            anchors_checked=None,
            retention_markers_verified=None,
            coverage_complete=False,
            limitations=(
                "audit_anchor_coverage_unverified",
                "audit_chain_unverified",
                "audit_retention_marker_unverified",
                "audit_retention_policy_unconfigured",
            ),
        ),
    )


class Soc2CertificationEvidenceCollector:
    """TRC-compatible local collector over an injected closed source reader."""

    collector_id = SOC2_CERTIFICATION_COLLECTOR_ID

    def __init__(
        self,
        bundle_reader: Callable[[], Soc2CertificationEvidenceBundle],
    ) -> None:
        if not callable(bundle_reader):
            raise TypeError("bundle_reader must be callable")
        self._bundle_reader = bundle_reader

    def collect_bundle(self) -> Soc2CertificationEvidenceBundle:
        """Read one bounded local bundle without exposing source exceptions."""
        try:
            bundle = self._bundle_reader()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return _unavailable_bundle()
        if not isinstance(bundle, Soc2CertificationEvidenceBundle):
            raise EvidenceCollectorValidationError(
                "SOC 2 certification source returned an invalid bundle"
            )
        return bundle

    def fetch(self, request: CollectionRequest, cursor: str | None) -> EvidencePage:
        validate_request(request)
        if request.collector_id != self.collector_id:
            raise EvidenceCollectorValidationError("collector request id is invalid")
        if cursor is not None:
            raise EvidenceCollectorValidationError(
                "SOC 2 certification collector is single-page"
            )
        if not set(request.control_refs).issubset(_CONTROL_REFS):
            raise EvidenceCollectorValidationError(
                "SOC 2 certification collector received an unsupported control"
            )
        bundle = self.collect_bundle()
        by_control = {item.control_ref: item for item in bundle.controls}
        items = tuple(by_control[control] for control in request.control_refs)
        source_revision = _sha256(
            {
                "schema": "lightwork.soc2-certification-page-source-revision.v1",
                "bundle_source_revision_sha256": bundle.source_revision_sha256,
                "controls": [
                    {
                        "control_ref": item.control_ref,
                        "source_revision_sha256": item.source_revision_sha256,
                    }
                    for item in items
                ],
            }
        )
        artifact = _sha256(
            {
                "schema": "lightwork.soc2-certification-page-artifact.v1",
                "bundle_evidence_sha256": bundle.evidence_sha256,
                "items": [asdict(item) for item in items],
            }
        )
        provider_request = _sha256(
            {
                "schema": "lightwork.soc2-certification-page-request.v1",
                "definition_id": request.definition_id,
                "definition_version": request.definition_version,
                "enrollment_id": request.enrollment_id,
                "enrollment_revision": request.enrollment_revision,
                "asset_scope_sha256": request.asset_scope_sha256,
                "control_refs": list(request.control_refs),
                "window_started_at": request.window_started_at,
                "window_ended_at": request.window_ended_at,
            }
        )
        page = EvidencePage(
            items=items,
            next_cursor=None,
            terminal=True,
            # Completion means the closed three-source bundle was reached.
            # Unknown/failed observations and limitations stay explicit.
            complete=True,
            incomplete_reason=None,
            source_revision_sha256=source_revision,
            artifact_sha256=artifact,
            bytes_read=len(_canonical([asdict(item) for item in items])),
            provider_request_sha256=provider_request,
        )
        return validate_page(page, expected_controls=request.control_refs)


@dataclass(frozen=True, slots=True)
class Soc2CertificationEvidenceSchedule:
    """One caller-persisted cadence declaration for the pure due selector."""

    schedule_id: str
    scope_sha256: str
    state: str
    interval_seconds: int
    next_due_at: float
    revision: int
    max_attempts: int

    def __post_init__(self) -> None:
        identifier = _id(self.schedule_id, "schedule_id")
        scope = _digest(self.scope_sha256, "schedule scope")
        if self.state not in _SCHEDULE_STATES:
            raise Soc2CertificationEvidenceValidationError("schedule state is invalid")
        interval = _integer(
            self.interval_seconds,
            "schedule interval_seconds",
            _MIN_INTERVAL_SECONDS,
            _MAX_INTERVAL_SECONDS,
        )
        due = _timestamp(self.next_due_at, "schedule next_due_at")
        revision = _integer(self.revision, "schedule revision", 1, 2**31 - 1)
        attempts = _integer(self.max_attempts, "schedule max_attempts", 1, _MAX_ATTEMPTS)
        object.__setattr__(self, "schedule_id", identifier)
        object.__setattr__(self, "scope_sha256", scope)
        object.__setattr__(self, "interval_seconds", interval)
        object.__setattr__(self, "next_due_at", due)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "max_attempts", attempts)


@dataclass(frozen=True, slots=True)
class Soc2CertificationEvidenceRunState:
    """Caller-persisted state for one deterministic scheduled run identity."""

    run_id: str
    schedule_id: str
    schedule_revision: int
    scheduled_for: float
    attempt: int
    state: str
    not_before: float
    lease_expires_at: float | None = None

    def __post_init__(self) -> None:
        identifier = _id(self.run_id, "run_id")
        schedule = _id(self.schedule_id, "run schedule_id")
        revision = _integer(self.schedule_revision, "run schedule revision", 1, 2**31 - 1)
        scheduled = _timestamp(self.scheduled_for, "run scheduled_for")
        attempt = _integer(self.attempt, "run attempt", 1, _MAX_ATTEMPTS)
        if self.state not in _RUN_STATES:
            raise Soc2CertificationEvidenceValidationError("run state is invalid")
        not_before = _timestamp(self.not_before, "run not_before")
        lease = (
            None
            if self.lease_expires_at is None
            else _timestamp(self.lease_expires_at, "run lease_expires_at")
        )
        if self.state == "leased" and lease is None:
            raise Soc2CertificationEvidenceValidationError(
                "leased run requires a lease expiry"
            )
        if self.state != "leased" and lease is not None:
            raise Soc2CertificationEvidenceValidationError(
                "non-leased run cannot carry a lease expiry"
            )
        object.__setattr__(self, "run_id", identifier)
        object.__setattr__(self, "schedule_id", schedule)
        object.__setattr__(self, "schedule_revision", revision)
        object.__setattr__(self, "scheduled_for", scheduled)
        object.__setattr__(self, "attempt", attempt)
        object.__setattr__(self, "not_before", not_before)
        object.__setattr__(self, "lease_expires_at", lease)


@dataclass(frozen=True, slots=True)
class Soc2CertificationDueRun:
    """A deterministic, retry-safe action a durable worker may persist."""

    run_id: str
    schedule_id: str
    schedule_revision: int
    scheduled_for: float
    attempt: int
    mode: str
    next_schedule_due_at: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _id(self.run_id, "due run_id"))
        object.__setattr__(self, "schedule_id", _id(self.schedule_id, "due schedule_id"))
        object.__setattr__(
            self,
            "schedule_revision",
            _integer(self.schedule_revision, "due schedule revision", 1, 2**31 - 1),
        )
        object.__setattr__(
            self,
            "scheduled_for",
            _timestamp(self.scheduled_for, "due scheduled_for"),
        )
        object.__setattr__(
            self,
            "attempt",
            _integer(self.attempt, "due attempt", 1, _MAX_ATTEMPTS),
        )
        if self.mode not in _DUE_MODES:
            raise Soc2CertificationEvidenceValidationError("due mode is invalid")
        advance = (
            None
            if self.next_schedule_due_at is None
            else _timestamp(self.next_schedule_due_at, "next schedule due")
        )
        object.__setattr__(self, "next_schedule_due_at", advance)


@dataclass(frozen=True, slots=True)
class Soc2CertificationScheduleAdvance:
    """A recovery-safe schedule advance for an already terminal due run."""

    schedule_id: str
    expected_revision: int
    expected_next_due_at: float
    next_due_at: float
    terminal_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schedule_id", _id(self.schedule_id, "advance schedule_id"))
        object.__setattr__(
            self,
            "expected_revision",
            _integer(self.expected_revision, "advance schedule revision", 1, 2**31 - 1),
        )
        expected = _timestamp(self.expected_next_due_at, "advance expected due")
        target = _timestamp(self.next_due_at, "advance next due")
        if target <= expected:
            raise Soc2CertificationEvidenceValidationError(
                "schedule advance must move forward"
            )
        object.__setattr__(self, "expected_next_due_at", expected)
        object.__setattr__(self, "next_due_at", target)
        object.__setattr__(self, "terminal_run_id", _id(self.terminal_run_id, "terminal run_id"))


@dataclass(frozen=True, slots=True)
class Soc2CertificationDueSelection:
    """Pure due/retry selection; callers must persist it with compare-and-set."""

    runs: tuple[Soc2CertificationDueRun, ...]
    terminal_advances: tuple[Soc2CertificationScheduleAdvance, ...]
    selected_at: float
    selection_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.runs, tuple) or any(
            not isinstance(item, Soc2CertificationDueRun) for item in self.runs
        ):
            raise Soc2CertificationEvidenceValidationError("due runs are invalid")
        keys = tuple(
            (item.scheduled_for, item.schedule_id, item.run_id) for item in self.runs
        )
        if keys != tuple(sorted(keys)):
            raise Soc2CertificationEvidenceValidationError("due runs are not deterministic")
        if not isinstance(self.terminal_advances, tuple) or any(
            not isinstance(item, Soc2CertificationScheduleAdvance)
            for item in self.terminal_advances
        ):
            raise Soc2CertificationEvidenceValidationError(
                "terminal schedule advances are invalid"
            )
        if tuple(item.schedule_id for item in self.terminal_advances) != tuple(
            sorted(item.schedule_id for item in self.terminal_advances)
        ):
            raise Soc2CertificationEvidenceValidationError(
                "terminal schedule advances are not deterministic"
            )
        selected = _timestamp(self.selected_at, "selector timestamp")
        supplied = _digest(self.selection_sha256, "selection reference")
        material = {
            "schema": SOC2_CERTIFICATION_SCHEDULE_SCHEMA,
            "runs": [asdict(item) for item in self.runs],
            "terminal_advances": [asdict(item) for item in self.terminal_advances],
            "selected_at": selected,
        }
        if supplied != _sha256(material):
            raise Soc2CertificationEvidenceValidationError(
                "selection reference does not match deterministic output"
            )
        object.__setattr__(self, "selected_at", selected)
        object.__setattr__(self, "selection_sha256", supplied)


def _scheduled_run_id(schedule: Soc2CertificationEvidenceSchedule, due_at: float) -> str:
    return "s2run-" + _sha256(
        {
            "schema": SOC2_CERTIFICATION_RUN_SCHEMA,
            "collector_id": SOC2_CERTIFICATION_COLLECTOR_ID,
            "schedule_id": schedule.schedule_id,
            "scope_sha256": schedule.scope_sha256,
            "schedule_revision": schedule.revision,
            "scheduled_for": due_at,
        }
    )[:48]


def _validated_schedule_state(
    schedules: Sequence[Soc2CertificationEvidenceSchedule],
    runs: Sequence[Soc2CertificationEvidenceRunState],
) -> tuple[
    dict[str, Soc2CertificationEvidenceSchedule],
    dict[str, Soc2CertificationEvidenceRunState],
]:
    if isinstance(schedules, (str, bytes)) or not isinstance(schedules, Sequence):
        raise Soc2CertificationEvidenceValidationError("schedules must be a sequence")
    if isinstance(runs, (str, bytes)) or not isinstance(runs, Sequence):
        raise Soc2CertificationEvidenceValidationError("runs must be a sequence")
    if any(not isinstance(item, Soc2CertificationEvidenceSchedule) for item in schedules):
        raise Soc2CertificationEvidenceValidationError("schedules are invalid")
    if any(not isinstance(item, Soc2CertificationEvidenceRunState) for item in runs):
        raise Soc2CertificationEvidenceValidationError("runs are invalid")
    schedules_by_id = {item.schedule_id: item for item in schedules}
    if len(schedules_by_id) != len(schedules):
        raise Soc2CertificationScheduleError("schedule ids must be unique")
    runs_by_id = {item.run_id: item for item in runs}
    if len(runs_by_id) != len(runs):
        raise Soc2CertificationScheduleError("run ids must be unique")
    return schedules_by_id, runs_by_id


def _retry_candidate(
    schedule: Soc2CertificationEvidenceSchedule,
    run: Soc2CertificationEvidenceRunState,
    *,
    now: float,
) -> Soc2CertificationDueRun | None:
    """Return a recovery action without allocating a second run identity."""
    if schedule.state != "active":
        return None
    mode: str | None = None
    attempt = run.attempt
    if run.state == "queued" and run.not_before <= now:
        mode = "queued_replay"
    elif run.state == "retry_wait" and run.not_before <= now:
        if run.attempt < schedule.max_attempts:
            mode = "retry_due"
            attempt += 1
    elif (
        run.state == "leased"
        and run.lease_expires_at is not None
        and run.lease_expires_at <= now
        and run.attempt < schedule.max_attempts
    ):
        mode = "lease_recovered"
        attempt += 1
    if mode is None:
        return None
    return Soc2CertificationDueRun(
        run_id=run.run_id,
        schedule_id=schedule.schedule_id,
        schedule_revision=schedule.revision,
        scheduled_for=run.scheduled_for,
        attempt=attempt,
        mode=mode,
        next_schedule_due_at=(
            _next_due(schedule.next_due_at, schedule.interval_seconds, now)
            if schedule.next_due_at <= run.scheduled_for
            else None
        ),
    )


def _live_retries(
    schedules_by_id: Mapping[str, Soc2CertificationEvidenceSchedule],
    runs: Sequence[Soc2CertificationEvidenceRunState],
    *,
    now: float,
) -> tuple[set[str], list[Soc2CertificationDueRun]]:
    live_schedule_ids: set[str] = set()
    candidates: list[Soc2CertificationDueRun] = []
    for run in runs:
        schedule = schedules_by_id.get(run.schedule_id)
        if schedule is None or schedule.revision != run.schedule_revision:
            continue
        if run.run_id != _scheduled_run_id(schedule, run.scheduled_for):
            raise Soc2CertificationScheduleError("run identity does not match schedule")
        if run.state not in _LIVE_RUN_STATES:
            continue
        if schedule.schedule_id in live_schedule_ids:
            raise Soc2CertificationScheduleError(
                "a schedule cannot have multiple live certification runs"
            )
        live_schedule_ids.add(schedule.schedule_id)
        candidate = _retry_candidate(schedule, run, now=now)
        if candidate is not None:
            candidates.append(candidate)
    return live_schedule_ids, candidates


def _due_schedule_actions(
    schedules: Sequence[Soc2CertificationEvidenceSchedule],
    runs_by_id: Mapping[str, Soc2CertificationEvidenceRunState],
    live_schedule_ids: set[str],
    *,
    now: float,
) -> tuple[list[Soc2CertificationDueRun], list[Soc2CertificationScheduleAdvance]]:
    candidates: list[Soc2CertificationDueRun] = []
    advances: list[Soc2CertificationScheduleAdvance] = []
    for schedule in sorted(schedules, key=lambda item: (item.next_due_at, item.schedule_id)):
        if (
            schedule.state != "active"
            or schedule.next_due_at > now
            or schedule.schedule_id in live_schedule_ids
        ):
            continue
        run_id = _scheduled_run_id(schedule, schedule.next_due_at)
        prior = runs_by_id.get(run_id)
        next_due = _next_due(schedule.next_due_at, schedule.interval_seconds, now)
        if prior is None:
            candidates.append(
                Soc2CertificationDueRun(
                    run_id=run_id,
                    schedule_id=schedule.schedule_id,
                    schedule_revision=schedule.revision,
                    scheduled_for=schedule.next_due_at,
                    attempt=1,
                    mode="new_due",
                    next_schedule_due_at=next_due,
                )
            )
            continue
        if (
            prior.schedule_id != schedule.schedule_id
            or prior.schedule_revision != schedule.revision
            or prior.scheduled_for != schedule.next_due_at
        ):
            raise Soc2CertificationScheduleError("terminal run does not match schedule")
        if prior.state in _RUN_STATES.difference(_LIVE_RUN_STATES):
            advances.append(
                Soc2CertificationScheduleAdvance(
                    schedule_id=schedule.schedule_id,
                    expected_revision=schedule.revision,
                    expected_next_due_at=schedule.next_due_at,
                    next_due_at=next_due,
                    terminal_run_id=prior.run_id,
                )
            )
    return candidates, advances


def select_due_soc2_certification_runs(
    schedules: Sequence[Soc2CertificationEvidenceSchedule],
    runs: Sequence[Soc2CertificationEvidenceRunState],
    *,
    now: float,
    limit: int = 100,
) -> Soc2CertificationDueSelection:
    """Select due and retry-safe work without mutating scheduler state.

    A caller must atomically persist a new/recovered run and, when
    next_schedule_due_at is present, advance the matching schedule only if its
    revision and next_due_at still equal the selected values. Repeated selection
    before that commit returns the same run id and attempt, not duplicate work.
    """
    moment = _timestamp(now, "selector now")
    maximum = _integer(limit, "selector limit", 1, _MAX_LIMIT)
    schedules_by_id, runs_by_id = _validated_schedule_state(schedules, runs)
    live_schedule_ids, retry_candidates = _live_retries(
        schedules_by_id,
        runs,
        now=moment,
    )
    due_candidates, terminal_advances = _due_schedule_actions(
        schedules,
        runs_by_id,
        live_schedule_ids,
        now=moment,
    )
    candidates = retry_candidates + due_candidates

    selected_runs = tuple(
        sorted(
            candidates,
            key=lambda item: (item.scheduled_for, item.schedule_id, item.run_id),
        )[:maximum]
    )
    selected_schedule_ids = {item.schedule_id for item in selected_runs}
    selected_advances = tuple(
        sorted(
            (
                item
                for item in terminal_advances
                if item.schedule_id not in selected_schedule_ids
            ),
            key=lambda item: item.schedule_id,
        )
    )
    material = {
        "schema": SOC2_CERTIFICATION_SCHEDULE_SCHEMA,
        "runs": [asdict(item) for item in selected_runs],
        "terminal_advances": [asdict(item) for item in selected_advances],
        "selected_at": moment,
    }
    return Soc2CertificationDueSelection(
        runs=selected_runs,
        terminal_advances=selected_advances,
        selected_at=moment,
        selection_sha256=_sha256(material),
    )


__all__ = [
    "AccessReviewState",
    "AuditLogRetentionPosture",
    "CERTIFICATION_ASSERTED",
    "GitChangeManagementSnapshot",
    "LIVE_AUDIT_ACCEPTANCE",
    "NETWORK_IO",
    "RAW_SOURCE_CONTENT_PERSISTED",
    "SOC2_ACCESS_REVIEW_CONTROL",
    "SOC2_AUDIT_RETENTION_CONTROL",
    "SOC2_CERTIFICATION_BUNDLE_SCHEMA",
    "SOC2_CERTIFICATION_COLLECTOR_ID",
    "SOC2_CERTIFICATION_COLLECTOR_SCHEMA",
    "SOC2_CERTIFICATION_NOTICE",
    "SOC2_CERTIFICATION_RUN_SCHEMA",
    "SOC2_CERTIFICATION_SCHEDULE_SCHEMA",
    "SOC2_CHANGE_MANAGEMENT_CONTROL",
    "Soc2CertificationDueRun",
    "Soc2CertificationDueSelection",
    "Soc2CertificationEvidenceBundle",
    "Soc2CertificationEvidenceCollector",
    "Soc2CertificationEvidenceError",
    "Soc2CertificationEvidenceSchedule",
    "Soc2CertificationEvidenceRunState",
    "Soc2CertificationEvidenceValidationError",
    "Soc2CertificationScheduleAdvance",
    "Soc2CertificationScheduleError",
    "Soc2EvidenceCoverage",
    "audit_retention_posture_from_soc2_snapshot",
    "capture_git_change_management_snapshot",
    "collect_soc2_certification_evidence",
    "select_due_soc2_certification_runs",
    "suite_audit_capture_reference",
]
