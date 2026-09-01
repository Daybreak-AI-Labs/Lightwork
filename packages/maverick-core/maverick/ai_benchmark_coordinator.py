"""Operable, privacy-gated coordinator for the AI benchmark exchange.

The benchmark client in :mod:`maverick.ai_benchmark_exchange_transport` is a
deliberately small outbound seam.  This module implements the other end of
that seam for pilots and self-hosted deployments.  It accepts only the
aggregate, pseudonymous export package that the client already validates.

Transport success is never treated as consent, inclusion, anonymity,
differential privacy, or privacy-budget evidence.  Before one packet can be
accepted, two distinct human reviewers must record a packet-digest-bound
review of each of those properties.  The current export contract explicitly
makes no anonymity or differential-privacy claim, so the only honest accepted
review state for those properties is ``reviewed_not_claimed`` (with privacy
budget ``not_applicable``).  A later protocol can add stronger, independently
verified states without silently upgrading these receipts.

The optional state file contains aggregate contribution packets, evidence
digests, hashed reviewer references, and receipts only.  It contains no raw
records, customer identifiers, tenant identifiers, credentials, or signing
keys.  Cohort reports are signed by an injected Ed25519 provider; private-key
custody remains outside this process.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .ai_benchmark_exchange import (
    COHORT_REPORT_SCHEMA,
    METHODOLOGY_VERSION,
)
from .ai_benchmark_exchange_transport import (
    COORDINATOR_RECEIPT_SCHEMA,
    COORDINATOR_SUBMISSION_PATH,
    COORDINATOR_SUBMISSION_SCHEMA,
    BenchmarkCoordinatorConsentError,
    BenchmarkCoordinatorIdempotencyConflict,
    BenchmarkCoordinatorProtocolError,
    CoordinatorHTTPResponse,
    Ed25519TrustKey,
    VersionedEd25519TrustRoots,
    _validate_export_package,
)
from .file_lock import (
    atomic_read_text,
    atomic_write_text,
    cross_process_lock,
    ensure_private_file,
)

PRIVACY_REVIEW_SCHEMA = "lightwork.ai-benchmark-privacy-review.v1"
COORDINATOR_STATE_SCHEMA = "lightwork.ai-benchmark-coordinator-state.v1"
TRUST_ROTATION_CEREMONY_SCHEMA = "lightwork.ai-benchmark-trust-rotation.v1"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID_RE = re.compile(r"KEY-[A-Za-z0-9_-]{16,96}\Z")
_PRINCIPAL_RE = re.compile(r"user:[A-Za-z0-9][A-Za-z0-9_.:@/+\-=]{0,127}\Z")
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}\Z")
_MAX_BODY_BYTES = 512 * 1024
_MIN_STATE_BYTES = 2 * 1024
_DEFAULT_STATE_BYTES = 64 * 1024 * 1024
_MAX_STATE_BYTES = 256 * 1024 * 1024
# Every state row has at least a 64-character key plus JSON punctuation.  This
# is a parser-allocation guard only; the canonical byte ceiling below is the
# authoritative, shared capacity across all three tables.
_MIN_STATE_ROW_BYTES = 68
_MAX_COUNT = 2**53 - 1


class BenchmarkCoordinatorStateError(RuntimeError):
    """Coordinator state or its privacy authority is invalid."""


class BenchmarkCoordinatorReviewRequired(BenchmarkCoordinatorConsentError):
    """The exact packet has no current, independently reviewed admission."""


class BenchmarkCoordinatorSigner(Protocol):
    """External Ed25519 signing seam; implementations retain key custody."""

    def __call__(self, payload: bytes, key_id: str) -> bytes: ...


def _canonical(value: object, *, maximum: int = _MAX_BODY_BYTES) -> bytes:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise BenchmarkCoordinatorProtocolError("coordinator value must be finite JSON") from exc
    if not 1 <= len(payload) <= maximum:
        raise BenchmarkCoordinatorProtocolError("coordinator value exceeds its byte bound")
    return payload


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: object, label: str) -> str:
    text = str(value or "").strip().lower().removeprefix("sha256:")
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{label} must be a SHA-256 digest")
    return text


def _principal_digest(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _PRINCIPAL_RE.fullmatch(text):
        raise ValueError(f"{label} must be an authenticated user principal")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
        or float(value) > 100_000_000_000.0
    ):
        raise ValueError(f"{label} must be a positive finite timestamp")
    return float(value)


@dataclass(frozen=True)
class BenchmarkPrivacyReview:
    """Two-person, exact-packet admission review.

    Evidence values are SHA-256 digests of separately retained review
    artifacts.  No property is inferred from TLS, an HTTP receipt, a cohort
    count, or a pseudonymous identifier.
    """

    packet_sha256: str
    contribution_consent_evidence_sha256: str
    inclusion_evidence_sha256: str
    anonymity_evidence_sha256: str
    differential_privacy_evidence_sha256: str
    privacy_budget_evidence_sha256: str
    inclusion_decision: str
    anonymity_review: str
    differential_privacy_review: str
    privacy_budget_review: str
    primary_reviewer: str
    independent_reviewer: str
    reviewed_at: float

    def normalized(self) -> dict[str, Any]:
        packet = _digest(self.packet_sha256, "packet_sha256")
        evidence = {
            "contribution_consent": _digest(
                self.contribution_consent_evidence_sha256,
                "contribution consent evidence",
            ),
            "inclusion": _digest(self.inclusion_evidence_sha256, "inclusion evidence"),
            "anonymity": _digest(self.anonymity_evidence_sha256, "anonymity evidence"),
            "differential_privacy": _digest(
                self.differential_privacy_evidence_sha256,
                "differential privacy evidence",
            ),
            "privacy_budget": _digest(
                self.privacy_budget_evidence_sha256,
                "privacy budget evidence",
            ),
        }
        if self.inclusion_decision != "included":
            raise ValueError("only an explicitly included contribution may be admitted")
        if self.anonymity_review != "reviewed_not_claimed":
            raise ValueError(
                "the current export contract permits only reviewed_not_claimed anonymity"
            )
        if self.differential_privacy_review != "reviewed_not_claimed":
            raise ValueError(
                "the current export contract permits only reviewed_not_claimed differential privacy"
            )
        if self.privacy_budget_review != "not_applicable":
            raise ValueError(
                "privacy budget must be not_applicable while differential privacy is not claimed"
            )
        first = _principal_digest(self.primary_reviewer, "primary reviewer")
        second = _principal_digest(self.independent_reviewer, "independent reviewer")
        if hmac.compare_digest(first, second):
            raise ValueError("benchmark privacy review requires two distinct humans")
        reviewed_at = _timestamp(self.reviewed_at, "reviewed_at")
        body = {
            "schema": PRIVACY_REVIEW_SCHEMA,
            "packet_sha256": packet,
            "contribution_consent_reviewed": True,
            "inclusion_decision": "included",
            "anonymity_review": "reviewed_not_claimed",
            "differential_privacy_review": "reviewed_not_claimed",
            "privacy_budget_review": "not_applicable",
            "evidence_sha256": evidence,
            "reviewer_sha256": [first, second],
            "reviewed_at": reviewed_at,
            "limitations": {
                "inclusion_proven_by_transport": False,
                "anonymity_verified": False,
                "differential_privacy_verified": False,
                "privacy_budget_accounted": False,
            },
        }
        body["review_sha256"] = _sha256(body)
        return body


def perform_trust_root_rotation(
    current: VersionedEd25519TrustRoots,
    *,
    version: int,
    additions: Sequence[Ed25519TrustKey] = (),
    revocations: Sequence[str] = (),
    primary_operator: str,
    independent_operator: str,
    ceremony_evidence_sha256: str,
    clock: Callable[[], float] = time.time,
) -> tuple[VersionedEd25519TrustRoots, dict[str, Any]]:
    """Advance trust roots and return a four-eyes, digest-bound ceremony record."""

    if not isinstance(current, VersionedEd25519TrustRoots):
        raise TypeError("current trust roots are invalid")
    first = _principal_digest(primary_operator, "primary operator")
    second = _principal_digest(independent_operator, "independent operator")
    if hmac.compare_digest(first, second):
        raise ValueError("trust-root rotation requires two distinct humans")
    evidence = _digest(ceremony_evidence_sha256, "ceremony evidence")
    rotated = current.advance(
        version=version,
        additions=additions,
        revocations=revocations,
    )
    before = current.as_document()
    after = rotated.as_document()
    record = {
        "schema": TRUST_ROTATION_CEREMONY_SCHEMA,
        "from_version": current.version,
        "to_version": rotated.version,
        "before_sha256": _sha256(before),
        "after_sha256": _sha256(after),
        "added_key_ids_sha256": sorted(
            hashlib.sha256(item.key_id.encode("utf-8")).hexdigest() for item in additions
        ),
        "revoked_key_ids_sha256": sorted(
            hashlib.sha256(item.encode("utf-8")).hexdigest() for item in revocations
        ),
        "operator_sha256": sorted((first, second)),
        "ceremony_evidence_sha256": evidence,
        "performed_at": _timestamp(clock(), "performed_at"),
        "private_keys_present": False,
    }
    record["ceremony_sha256"] = _sha256(record)
    return rotated, record


class BenchmarkCoordinatorService:
    """Aggregate-only coordinator with explicit privacy-review admission."""

    def __init__(
        self,
        *,
        signer: BenchmarkCoordinatorSigner,
        signing_key_id: str,
        state_path: str | Path | None = None,
        clock: Callable[[], float] = time.time,
        max_state_bytes: int = _DEFAULT_STATE_BYTES,
    ) -> None:
        if not callable(signer) or not callable(clock):
            raise TypeError("coordinator signer and clock must be callable")
        if not isinstance(signing_key_id, str) or not _KEY_ID_RE.fullmatch(signing_key_id):
            raise ValueError("coordinator signing key id is invalid")
        if (
            isinstance(max_state_bytes, bool)
            or not isinstance(max_state_bytes, int)
            or not _MIN_STATE_BYTES <= max_state_bytes <= _MAX_STATE_BYTES
        ):
            raise ValueError("coordinator state byte limit must be between 2 KiB and 256 MiB")
        self._signer = signer
        self._signing_key_id = signing_key_id
        self._clock = clock
        self._max_state_bytes = max_state_bytes
        self._state_path = Path(state_path) if state_path is not None else None
        self._lock = threading.RLock()
        self._state = self._load_state()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema": COORDINATOR_STATE_SCHEMA,
            "reviews": {},
            "contributions": {},
            "receipts": {},
        }

    def _validated_state(self, value: object) -> dict[str, Any]:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema", "reviews", "contributions", "receipts"}
            or value.get("schema") != COORDINATOR_STATE_SCHEMA
        ):
            raise BenchmarkCoordinatorStateError("coordinator state shape is invalid")
        state = copy.deepcopy(dict(value))
        maximum_rows = self._max_state_bytes // _MIN_STATE_ROW_BYTES
        for label in ("reviews", "contributions", "receipts"):
            rows = state[label]
            if not isinstance(rows, dict) or len(rows) > maximum_rows:
                raise BenchmarkCoordinatorStateError(
                    f"coordinator {label} row count exceeds its byte-derived bound"
                )
        for packet, review in state["reviews"].items():
            if not _DIGEST_RE.fullmatch(str(packet)) or not isinstance(review, dict):
                raise BenchmarkCoordinatorStateError("coordinator review state is invalid")
            if review.get("schema") != PRIVACY_REVIEW_SCHEMA:
                raise BenchmarkCoordinatorStateError("coordinator review schema is invalid")
            if review.get("packet_sha256") != packet:
                raise BenchmarkCoordinatorStateError("coordinator review binding is invalid")
            claimed = review.get("review_sha256")
            body = {key: item for key, item in review.items() if key != "review_sha256"}
            if claimed != _sha256(body):
                raise BenchmarkCoordinatorStateError("coordinator review digest is invalid")
        for packet, contribution in state["contributions"].items():
            if not _DIGEST_RE.fullmatch(str(packet)) or not isinstance(contribution, Mapping):
                raise BenchmarkCoordinatorStateError("coordinator contribution state is invalid")
            normalized = _validate_export_package(contribution)
            if normalized["packet_sha256"] != packet:
                raise BenchmarkCoordinatorStateError("coordinator contribution binding is invalid")
            state["contributions"][packet] = normalized
        for operation, stored in state["receipts"].items():
            if not _IDEMPOTENCY_RE.fullmatch(str(operation)) or not isinstance(stored, Mapping):
                raise BenchmarkCoordinatorStateError("coordinator receipt state is invalid")
            if set(stored) != {"request_sha256", "receipt"}:
                raise BenchmarkCoordinatorStateError("coordinator receipt shape is invalid")
            _digest(stored["request_sha256"], "request_sha256")
            receipt = stored["receipt"]
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("schema") != COORDINATOR_RECEIPT_SCHEMA
                or receipt.get("idempotency_key") != operation
            ):
                raise BenchmarkCoordinatorStateError("coordinator receipt is invalid")
        self._state_payload(state)
        return state

    def _state_payload(self, state: Mapping[str, Any]) -> bytes:
        """Serialize against the state-file ceiling, not the request ceiling."""

        try:
            return _canonical(state, maximum=self._max_state_bytes)
        except BenchmarkCoordinatorProtocolError as exc:
            raise BenchmarkCoordinatorStateError(
                "coordinator durable state byte capacity reached"
            ) from exc

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path
        if path is None or not path.exists():
            return self._empty_state()
        ensure_private_file(path, 0o600)
        try:
            if path.stat().st_size > self._max_state_bytes:
                raise BenchmarkCoordinatorStateError(
                    "coordinator durable state byte capacity exceeded"
                )
            raw = atomic_read_text(path, encoding="utf-8")
            if len(raw.encode("utf-8")) > self._max_state_bytes:
                raise BenchmarkCoordinatorStateError(
                    "coordinator durable state byte capacity exceeded"
                )
            loaded = json.loads(raw)
        except BenchmarkCoordinatorStateError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise BenchmarkCoordinatorStateError("coordinator state could not be read") from exc
        return self._validated_state(loaded)

    def _save_state(self, payload: bytes) -> None:
        if self._state_path is None:
            return
        try:
            atomic_write_text(
                self._state_path,
                payload.decode("ascii"),
                mode=0o600,
                encoding="utf-8",
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise BenchmarkCoordinatorStateError(
                "coordinator durable state could not be saved"
            ) from None

    def _mutate(self, operation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            if self._state_path is not None:
                with cross_process_lock(self._state_path, strict=True):
                    return self._mutate_locked(operation, self._load_state())
            return self._mutate_locked(operation, self._state)

    def _mutate_locked(
        self,
        operation: Callable[[dict[str, Any]], Any],
        base: dict[str, Any],
    ) -> Any:
        """Commit a fully serializable copy or leave memory and disk unchanged."""

        candidate = copy.deepcopy(base)
        try:
            result = operation(candidate)
            payload = self._state_payload(candidate)
            self._save_state(payload)
        except BaseException:
            self._state = base
            raise
        self._state = candidate
        return result

    def admit_review(
        self,
        export_package: Mapping[str, Any],
        review: BenchmarkPrivacyReview,
    ) -> dict[str, Any]:
        """Record one two-person review for the exact canonical packet."""

        contribution = _validate_export_package(export_package)
        packet = contribution["packet_sha256"]
        normalized = review.normalized()
        if not hmac.compare_digest(normalized["packet_sha256"], packet):
            raise BenchmarkCoordinatorStateError(
                "privacy review belongs to a different contribution packet"
            )
        privacy = contribution["privacy"]
        if (
            privacy["anonymity_claim"] is not False
            or privacy["differential_privacy_claim"] is not False
            or privacy["privacy_budget_accounting"] != "unsupported"
        ):
            raise BenchmarkCoordinatorStateError(
                "privacy review does not support the packet's claims"
            )

        def store(state: dict[str, Any]) -> dict[str, Any]:
            existing = state["reviews"].get(packet)
            if existing is not None and existing != normalized:
                raise BenchmarkCoordinatorIdempotencyConflict(
                    "packet already has a different privacy review"
                )
            state["reviews"][packet] = copy.deepcopy(normalized)
            return copy.deepcopy(normalized)

        return self._mutate(store)

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        selected = [
            value
            for key, value in headers.items()
            if isinstance(key, str) and isinstance(value, str) and key.lower() == name.lower()
        ]
        if len(selected) > 1:
            raise BenchmarkCoordinatorProtocolError(
                "coordinator request repeated a security header"
            )
        return selected[0] if selected else None

    def handle_submission(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> CoordinatorHTTPResponse:
        """Handle the closed client protocol and return a typed HTTP response."""

        if not isinstance(headers, Mapping) or not isinstance(body, bytes):
            raise BenchmarkCoordinatorProtocolError("coordinator request is invalid")
        if not 1 <= len(body) <= _MAX_BODY_BYTES:
            raise BenchmarkCoordinatorProtocolError("coordinator request exceeds its bound")
        content_type = self._header(headers, "content-type")
        if (
            not isinstance(content_type, str)
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            raise BenchmarkCoordinatorProtocolError(
                "coordinator request Content-Type must be application/json"
            )
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise BenchmarkCoordinatorProtocolError("coordinator request JSON is invalid") from exc
        if _canonical(decoded) != body:
            raise BenchmarkCoordinatorProtocolError("coordinator request must use canonical JSON")
        if (
            not isinstance(decoded, Mapping)
            or set(decoded) != {"schema", "explicit_consent", "idempotency_key", "contribution"}
            or decoded.get("schema") != COORDINATOR_SUBMISSION_SCHEMA
            or decoded.get("explicit_consent") is not True
        ):
            raise BenchmarkCoordinatorConsentError(
                "coordinator request lacks literal packet consent"
            )
        operation = str(decoded.get("idempotency_key") or "")
        if not _IDEMPOTENCY_RE.fullmatch(operation):
            raise BenchmarkCoordinatorProtocolError(
                "coordinator request idempotency key is invalid"
            )
        header_operation = self._header(headers, "idempotency-key")
        if header_operation != operation:
            raise BenchmarkCoordinatorIdempotencyConflict(
                "coordinator header and body idempotency keys differ"
            )
        contribution = _validate_export_package(decoded["contribution"])
        packet = contribution["packet_sha256"]
        if self._header(headers, "x-lightwork-packet-sha256") != packet:
            raise BenchmarkCoordinatorIdempotencyConflict(
                "coordinator packet header binding differs"
            )
        if self._header(headers, "x-lightwork-explicit-consent") != "true":
            raise BenchmarkCoordinatorConsentError("coordinator consent header is missing")
        request_digest = hashlib.sha256(body).hexdigest()
        now = _timestamp(self._clock(), "accepted_at")

        def accept(state: dict[str, Any]) -> dict[str, Any]:
            prior = state["receipts"].get(operation)
            if prior is not None:
                if not hmac.compare_digest(prior["request_sha256"], request_digest):
                    raise BenchmarkCoordinatorIdempotencyConflict(
                        "coordinator idempotency key is bound to different content"
                    )
                duplicate = copy.deepcopy(prior["receipt"])
                duplicate["status"] = "duplicate"
                return duplicate
            review = state["reviews"].get(packet)
            if review is None:
                raise BenchmarkCoordinatorReviewRequired(
                    "contribution requires consent, inclusion, anonymity, differential-privacy, and privacy-budget review"
                )
            if review.get("limitations") != {
                "inclusion_proven_by_transport": False,
                "anonymity_verified": False,
                "differential_privacy_verified": False,
                "privacy_budget_accounted": False,
            }:
                raise BenchmarkCoordinatorStateError("privacy review limitations were changed")
            if packet not in state["contributions"]:
                state["contributions"][packet] = copy.deepcopy(contribution)
            receipt = {
                "schema": COORDINATOR_RECEIPT_SCHEMA,
                "status": "accepted",
                "receipt_id": "BCR-"
                + _sha256({"packet_sha256": packet, "idempotency_key": operation})[:32],
                "idempotency_key": operation,
                "packet_sha256": packet,
                "accepted_at": now,
            }
            state["receipts"][operation] = {
                "request_sha256": request_digest,
                "receipt": copy.deepcopy(receipt),
            }
            return receipt

        receipt = self._mutate(accept)
        payload = _canonical(receipt)
        return CoordinatorHTTPResponse(
            200,
            {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(payload)),
            },
            payload,
        )

    def build_cohort_report(
        self,
        *,
        metric_code: str,
        period: Mapping[str, str],
        cohort: Mapping[str, str],
        minimum_contributors: int = 5,
    ) -> dict[str, Any]:
        """Aggregate accepted packets and sign an explicitly non-DP report."""

        if (
            isinstance(minimum_contributors, bool)
            or not isinstance(minimum_contributors, int)
            or not 5 <= minimum_contributors <= 10_000
        ):
            raise ValueError("minimum_contributors must be between 5 and 10000")
        selected_period = dict(period)
        selected_cohort = dict(cohort)
        with self._lock:
            if self._state_path is not None:
                with cross_process_lock(self._state_path, strict=True):
                    self._state = self._load_state()
            rows = [
                copy.deepcopy(item)
                for item in self._state["contributions"].values()
                if item["metric_code"] == metric_code
                and item["period"] == selected_period
                and item["cohort"] == selected_cohort
            ]
        identities = {item["pseudonymous_contribution_id"] for item in rows}
        if len(identities) != len(rows):
            raise BenchmarkCoordinatorStateError(
                "cohort contains duplicate pseudonymous contributors"
            )
        if len(rows) < minimum_contributors:
            raise BenchmarkCoordinatorReviewRequired(
                "cohort does not satisfy the reviewed minimum contributor threshold"
            )
        numerator = sum(int(item["numerator"]) for item in rows)
        denominator = sum(int(item["denominator"]) for item in rows)
        if denominator <= 0 or numerator > denominator or denominator > _MAX_COUNT:
            raise BenchmarkCoordinatorStateError("cohort aggregate exceeds its bound")
        issued_at = _timestamp(self._clock(), "issued_at")
        seed = {
            "packet_sha256": sorted(item["packet_sha256"] for item in rows),
            "issued_at": issued_at,
            "minimum_contributors": minimum_contributors,
        }
        body = {
            "schema": COHORT_REPORT_SCHEMA,
            "report_id": "BXR-" + _sha256(seed)[:32],
            "issued_at": issued_at,
            "metric_code": metric_code,
            "numerator": numerator,
            "denominator": denominator,
            "period": selected_period,
            "cohort": selected_cohort,
            "contributor_count": len(rows),
            "k_anonymity_min": minimum_contributors,
            "aggregation_method": "pooled_ratio",
            "privacy": {
                "state": "not_dp",
                "epsilon": None,
                "delta": None,
                "noise_mechanism": None,
            },
            "methodology_version": METHODOLOGY_VERSION,
        }
        payload = _canonical(body)
        signature = self._signer(payload, self._signing_key_id)
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise BenchmarkCoordinatorStateError(
                "coordinator signer returned an invalid Ed25519 signature"
            )
        return {
            **body,
            "report_sha256": hashlib.sha256(payload).hexdigest(),
            "signature": {
                "algorithm": "Ed25519",
                "key_id": self._signing_key_id,
                "value": signature.hex(),
            },
        }

    def status(self) -> dict[str, Any]:
        """Return counts and honest proof limits without exposing review data."""

        with self._lock:
            if self._state_path is not None:
                with cross_process_lock(self._state_path, strict=True):
                    self._state = self._load_state()
            return {
                "schema": "lightwork.ai-benchmark-coordinator-status.v1",
                "reviews": len(self._state["reviews"]),
                "accepted_contributions": len(self._state["contributions"]),
                "receipts": len(self._state["receipts"]),
                "durable": self._state_path is not None,
                "raw_records_stored": False,
                "tenant_identifiers_stored": False,
                "transport_proves_inclusion": False,
                "anonymity_verified": False,
                "differential_privacy_verified": False,
                "privacy_budget_accounting": "unsupported",
            }


class InProcessBenchmarkCoordinatorTransport:
    """Protocol-faithful transport for a local coordinator pilot/test estate."""

    def __init__(self, service: BenchmarkCoordinatorService) -> None:
        if not isinstance(service, BenchmarkCoordinatorService):
            raise TypeError("benchmark coordinator service is invalid")
        self._service = service

    def post(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        allow_redirects: bool,
        max_response_bytes: int,
    ) -> CoordinatorHTTPResponse:
        if not isinstance(url, str) or not url.endswith(COORDINATOR_SUBMISSION_PATH):
            raise BenchmarkCoordinatorProtocolError("in-process coordinator path is invalid")
        if allow_redirects:
            raise BenchmarkCoordinatorProtocolError(
                "in-process coordinator must not follow redirects"
            )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or float(timeout) <= 0
            or not isinstance(max_response_bytes, int)
            or max_response_bytes <= 0
        ):
            raise BenchmarkCoordinatorProtocolError("in-process coordinator limits are invalid")
        try:
            response = self._service.handle_submission(headers=headers, body=body)
        except BenchmarkCoordinatorIdempotencyConflict:
            payload = b"{}"
            response = CoordinatorHTTPResponse(
                409,
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                },
                payload,
            )
        except (BenchmarkCoordinatorReviewRequired, BenchmarkCoordinatorConsentError):
            payload = b"{}"
            response = CoordinatorHTTPResponse(
                403,
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                },
                payload,
            )
        except (BenchmarkCoordinatorProtocolError, BenchmarkCoordinatorStateError):
            payload = b"{}"
            response = CoordinatorHTTPResponse(
                400,
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                },
                payload,
            )
        if len(response.body) > max_response_bytes:
            raise BenchmarkCoordinatorProtocolError(
                "in-process coordinator response exceeds the client bound"
            )
        return response


__all__ = [
    "BenchmarkCoordinatorReviewRequired",
    "BenchmarkCoordinatorService",
    "BenchmarkCoordinatorSigner",
    "BenchmarkCoordinatorStateError",
    "BenchmarkPrivacyReview",
    "COORDINATOR_STATE_SCHEMA",
    "InProcessBenchmarkCoordinatorTransport",
    "PRIVACY_REVIEW_SCHEMA",
    "TRUST_ROTATION_CEREMONY_SCHEMA",
    "perform_trust_root_rotation",
]
