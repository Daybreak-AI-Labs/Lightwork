"""Typed, signed receipts for production consequential actions.

Production must never infer that a remote mutation committed from display text.
This module defines the exact receipt exchanged between the governed dispatch
boundary and a connector, binds it to the authenticated execution context, and
provides a durable one-use claim that prevents automatic replay across workers
or process restarts.

The current production manifest keeps consequential actions disabled.  These
types are the fail-closed protocol a future reviewed mutation tool must adopt
before that manifest constraint can be enabled.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

RECEIPT_SCHEMA = "lightwork.consequential-receipt.v1"
CLAIM_SCHEMA = "lightwork.consequential-receipt-claim.v1"
MAX_RECEIPT_TTL_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 30
DEFAULT_RECEIPT_TTL_SECONDS = 120
ZERO_SHA256 = "0" * 64

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_KEY_ID_RE = re.compile(r"[0-9a-f]{16}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_OPERATION_ID_RE = re.compile(r"[0-9a-f]{32}")
_BODY_FIELDS = {
    "schema",
    "state",
    "tenant_id",
    "principal_id",
    "tool_name",
    "action",
    "target",
    "request_sha256",
    "idempotency_key",
    "operation_id",
    "policy_decision",
    "policy_source",
    "policy_actor",
    "policy_evidence_sha256",
    "issued_at",
    "expires_at",
    "external_reference",
    "reconciliation_method",
    "previous_receipt_sha256",
    "provenance_provider",
    "provenance_authority",
    "provenance_subject",
    "provenance_evidence_sha256",
    "provenance_authenticated",
    "signer_authority",
}
_SIGNED_FIELDS = _BODY_FIELDS | {
    "payload_sha256",
    "signer_key_id",
    "signature",
    "receipt_sha256",
}


class ConsequentialReceiptError(RuntimeError):
    """A consequential-action receipt is absent, malformed, or untrusted."""


class ReceiptReplayError(ConsequentialReceiptError):
    """A valid receipt was already consumed by this deployment."""


class ReceiptState(str, Enum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    REFUSED = "REFUSED"
    INDETERMINATE = "INDETERMINATE"


class PolicyDecision(str, Enum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"


def _canonical(value: Mapping[str, Any] | dict[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConsequentialReceiptError(
            "consequential receipt value is not canonical JSON"
        ) from exc


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ConsequentialReceiptError(f"receipt {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConsequentialReceiptError(f"receipt {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise ConsequentialReceiptError(f"receipt {field} has no timezone")
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str, *, maximum: int, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConsequentialReceiptError(f"receipt {field} must be a string")
    if (not empty and not value) or len(value) > maximum:
        raise ConsequentialReceiptError(f"receipt {field} has invalid length")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConsequentialReceiptError(f"receipt {field} contains control characters")
    return value


@dataclass(frozen=True)
class ConsequentialReceipt:
    """Exact signed representation of one production mutation transition."""

    schema: str
    state: ReceiptState
    tenant_id: str
    principal_id: str
    tool_name: str
    action: str
    target: str
    request_sha256: str
    idempotency_key: str
    operation_id: str
    policy_decision: PolicyDecision
    policy_source: str
    policy_actor: str
    policy_evidence_sha256: str
    issued_at: str
    expires_at: str
    external_reference: str
    reconciliation_method: str
    previous_receipt_sha256: str
    provenance_provider: str
    provenance_authority: str
    provenance_subject: str
    provenance_evidence_sha256: str
    provenance_authenticated: bool
    signer_authority: str
    payload_sha256: str
    signer_key_id: str
    signature: str
    receipt_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "state": self.state.value,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "tool_name": self.tool_name,
            "action": self.action,
            "target": self.target,
            "request_sha256": self.request_sha256,
            "idempotency_key": self.idempotency_key,
            "operation_id": self.operation_id,
            "policy_decision": self.policy_decision.value,
            "policy_source": self.policy_source,
            "policy_actor": self.policy_actor,
            "policy_evidence_sha256": self.policy_evidence_sha256,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "external_reference": self.external_reference,
            "reconciliation_method": self.reconciliation_method,
            "previous_receipt_sha256": self.previous_receipt_sha256,
            "provenance_provider": self.provenance_provider,
            "provenance_authority": self.provenance_authority,
            "provenance_subject": self.provenance_subject,
            "provenance_evidence_sha256": self.provenance_evidence_sha256,
            "provenance_authenticated": self.provenance_authenticated,
            "signer_authority": self.signer_authority,
            "payload_sha256": self.payload_sha256,
            "signer_key_id": self.signer_key_id,
            "signature": self.signature,
            "receipt_sha256": self.receipt_sha256,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ConsequentialReceipt:
        """Parse only the exact schema; unknown and missing fields are fatal."""
        if not isinstance(value, Mapping) or set(value) != _SIGNED_FIELDS:
            raise ConsequentialReceiptError(
                "consequential receipt has missing or unexpected fields"
            )
        try:
            state = ReceiptState(value.get("state"))
            decision = PolicyDecision(value.get("policy_decision"))
        except (TypeError, ValueError) as exc:
            raise ConsequentialReceiptError("receipt state or decision is invalid") from exc
        return cls(
            schema=str(value.get("schema")),
            state=state,
            tenant_id=_text(value.get("tenant_id"), "tenant_id", maximum=256),
            principal_id=_text(value.get("principal_id"), "principal_id", maximum=512),
            tool_name=_text(value.get("tool_name"), "tool_name", maximum=256),
            action=_text(value.get("action"), "action", maximum=512),
            target=_text(value.get("target"), "target", maximum=8192),
            request_sha256=str(value.get("request_sha256")),
            idempotency_key=str(value.get("idempotency_key")),
            operation_id=str(value.get("operation_id")),
            policy_decision=decision,
            policy_source=_text(value.get("policy_source"), "policy_source", maximum=128),
            policy_actor=_text(value.get("policy_actor"), "policy_actor", maximum=512, empty=True),
            policy_evidence_sha256=str(value.get("policy_evidence_sha256")),
            issued_at=_text(value.get("issued_at"), "issued_at", maximum=64),
            expires_at=_text(value.get("expires_at"), "expires_at", maximum=64),
            external_reference=_text(
                value.get("external_reference"),
                "external_reference",
                maximum=2048,
                empty=True,
            ),
            reconciliation_method=_text(
                value.get("reconciliation_method"),
                "reconciliation_method",
                maximum=128,
            ),
            previous_receipt_sha256=str(value.get("previous_receipt_sha256")),
            provenance_provider=_text(
                value.get("provenance_provider"),
                "provenance_provider",
                maximum=256,
            ),
            provenance_authority=_text(
                value.get("provenance_authority"),
                "provenance_authority",
                maximum=512,
            ),
            provenance_subject=_text(
                value.get("provenance_subject"),
                "provenance_subject",
                maximum=512,
            ),
            provenance_evidence_sha256=str(value.get("provenance_evidence_sha256")),
            provenance_authenticated=value.get("provenance_authenticated"),
            signer_authority=_text(value.get("signer_authority"), "signer_authority", maximum=128),
            payload_sha256=str(value.get("payload_sha256")),
            signer_key_id=str(value.get("signer_key_id")),
            signature=str(value.get("signature")),
            receipt_sha256=str(value.get("receipt_sha256")),
        )


@dataclass(frozen=True)
class ReceiptExpectation:
    """Authoritative dispatch context a receipt must match exactly."""

    tenant_id: str
    principal_id: str
    tool_name: str
    action: str
    target: str
    request_sha256: str
    idempotency_key: str
    operation_id: str
    reconciliation_method: str
    allowed_states: frozenset[ReceiptState]
    previous_receipt_sha256: str | None


@dataclass(frozen=True)
class ProviderMutationReceipt:
    """Normalized evidence returned by a reviewed connector/provider adapter.

    A provider adapter may construct this only after authenticating the
    provider response or authoritative reconciliation read.  Display strings,
    HTTP status text, and exception messages are not commit evidence.
    """

    state: ReceiptState
    idempotency_key: str
    provider: str
    authority: str
    subject: str
    evidence_sha256: str
    external_reference: str
    reconciliation_method: str
    authenticated: bool
    display: str

    def validate(
        self,
        *,
        expected_idempotency_key: str,
        expected_reconciliation_method: str,
    ) -> None:
        if self.state not in {
            ReceiptState.COMMITTED,
            ReceiptState.REFUSED,
            ReceiptState.INDETERMINATE,
        }:
            raise ConsequentialReceiptError("provider receipt state is invalid")
        if self.idempotency_key != expected_idempotency_key:
            raise ConsequentialReceiptError("provider receipt idempotency key mismatches")
        if self.reconciliation_method != expected_reconciliation_method:
            raise ConsequentialReceiptError("provider receipt reconciliation mismatches")
        _text(self.provider, "provider", maximum=256)
        _text(self.authority, "provider_authority", maximum=512)
        _text(self.subject, "provider_subject", maximum=512)
        _text(self.display, "provider_display", maximum=4096, empty=True)
        if not self.authenticated:
            raise ConsequentialReceiptError("provider receipt provenance is unauthenticated")
        if not _SHA256_RE.fullmatch(self.evidence_sha256):
            raise ConsequentialReceiptError("provider receipt evidence digest is invalid")
        if self.state is ReceiptState.COMMITTED and not self.external_reference:
            raise ConsequentialReceiptError(
                "committed provider receipt lacks an external reference"
            )
        _text(
            self.external_reference,
            "provider_external_reference",
            maximum=2048,
            empty=True,
        )


class ConsequentialToolResult(str):
    """Wire-compatible display text carrying an authoritative typed receipt."""

    receipt: ConsequentialReceipt

    def __new__(
        cls,
        display: str,
        receipt: ConsequentialReceipt,
    ) -> ConsequentialToolResult:
        obj = super().__new__(cls, str(display))
        obj.receipt = receipt
        return obj


def canonical_request(args: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the JSON request at the production external-effect boundary."""
    if not isinstance(args, Mapping):
        raise ConsequentialReceiptError("consequential tool arguments must be an object")
    frozen = dict(args)
    frozen.pop("_maverick_idempotency_key", None)
    frozen["confirm"] = True
    # Round-trip produces a detached, JSON-only object and rejects NaN/custom
    # values instead of silently signing a lossy ``default=str`` rendering.
    try:
        encoded = json.dumps(
            frozen,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConsequentialReceiptError("consequential request is not strict JSON") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - mapping encoded above
        raise ConsequentialReceiptError("consequential request is not an object")
    return decoded


def request_sha256(args: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(args)).encode("utf-8")).hexdigest()


def receipt_action(tool_name: str, args: Mapping[str, Any]) -> str:
    name = _text(str(tool_name), "tool_name", maximum=256)
    op = str(args.get("op") or "write").strip().lower()
    _text(op, "operation", maximum=240)
    return f"{name}.{op}"


def receipt_target(policy: Any, args: dict[str, Any]) -> str:
    try:
        paths, hosts = policy.extract_resources(args)
    except Exception as exc:
        raise ConsequentialReceiptError("consequential action target extraction failed") from exc
    return _canonical(
        {
            "hosts": sorted(set(hosts)),
            "paths": sorted(set(paths)),
        }
    )


def derive_idempotency_key(
    *,
    tenant_id: str,
    principal_id: str,
    tool_name: str,
    action: str,
    target: str,
    request_digest: str,
) -> str:
    binding = {
        "action": action,
        "principal_id": principal_id,
        "request_sha256": request_digest,
        "target": target,
        "tenant_id": tenant_id,
        "tool_name": tool_name,
    }
    return hashlib.sha256(_canonical(binding).encode("utf-8")).hexdigest()


def evidence_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _validate_receipt_shape(
    receipt: ConsequentialReceipt,
    *,
    now: datetime,
) -> None:
    if receipt.schema != RECEIPT_SCHEMA:
        raise ConsequentialReceiptError("receipt schema is unsupported")
    for field, value in (
        ("request_sha256", receipt.request_sha256),
        ("idempotency_key", receipt.idempotency_key),
        ("policy_evidence_sha256", receipt.policy_evidence_sha256),
        ("previous_receipt_sha256", receipt.previous_receipt_sha256),
        ("provenance_evidence_sha256", receipt.provenance_evidence_sha256),
        ("payload_sha256", receipt.payload_sha256),
        ("receipt_sha256", receipt.receipt_sha256),
    ):
        if not _SHA256_RE.fullmatch(value):
            raise ConsequentialReceiptError(f"receipt {field} is invalid")
    if not _OPERATION_ID_RE.fullmatch(receipt.operation_id):
        raise ConsequentialReceiptError("receipt operation_id is invalid")
    if not _KEY_ID_RE.fullmatch(receipt.signer_key_id):
        raise ConsequentialReceiptError("receipt signer_key_id is invalid")
    if not _SIGNATURE_RE.fullmatch(receipt.signature):
        raise ConsequentialReceiptError("receipt signature encoding is invalid")
    if receipt.provenance_authenticated is not True:
        raise ConsequentialReceiptError("receipt provenance is unauthenticated")
    issued = _parse_utc(receipt.issued_at, "issued_at")
    expires = _parse_utc(receipt.expires_at, "expires_at")
    if expires <= issued or expires > issued + timedelta(seconds=MAX_RECEIPT_TTL_SECONDS):
        raise ConsequentialReceiptError("receipt validity interval is invalid")
    if issued > now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise ConsequentialReceiptError("receipt was issued too far in the future")
    if expires < now:
        raise ConsequentialReceiptError("receipt is stale")
    if receipt.state is ReceiptState.PREPARED:
        if (
            receipt.policy_decision is not PolicyDecision.GRANTED
            or receipt.previous_receipt_sha256 != ZERO_SHA256
            or receipt.external_reference
        ):
            raise ConsequentialReceiptError("prepared receipt transition is invalid")
    elif receipt.state is ReceiptState.COMMITTED:
        if (
            receipt.policy_decision is not PolicyDecision.GRANTED
            or receipt.previous_receipt_sha256 == ZERO_SHA256
            or not receipt.external_reference
        ):
            raise ConsequentialReceiptError("committed receipt transition is invalid")
    elif receipt.state is ReceiptState.INDETERMINATE:
        if (
            receipt.policy_decision is not PolicyDecision.GRANTED
            or receipt.previous_receipt_sha256 == ZERO_SHA256
        ):
            raise ConsequentialReceiptError("indeterminate receipt transition is invalid")
    elif receipt.state is ReceiptState.REFUSED:
        if receipt.external_reference:
            raise ConsequentialReceiptError("refused receipt has an external reference")


def issue_receipt(
    *,
    state: ReceiptState,
    tenant_id: str,
    principal_id: str,
    tool_name: str,
    action: str,
    target: str,
    request_digest: str,
    idempotency_key: str,
    operation_id: str,
    policy_decision: PolicyDecision,
    policy_source: str,
    policy_actor: str,
    policy_evidence_sha256: str,
    external_reference: str,
    reconciliation_method: str,
    previous_receipt_sha256: str,
    provenance_provider: str,
    provenance_authority: str,
    provenance_subject: str,
    provenance_evidence_sha256: str,
    provenance_authenticated: bool,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_RECEIPT_TTL_SECONDS,
) -> ConsequentialReceipt:
    """Issue a receipt signed by the active governed audit key."""
    issued = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= MAX_RECEIPT_TTL_SECONDS
    ):
        raise ConsequentialReceiptError("receipt TTL is invalid")
    body: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "state": state.value,
        "tenant_id": tenant_id,
        "principal_id": principal_id,
        "tool_name": tool_name,
        "action": action,
        "target": target,
        "request_sha256": request_digest,
        "idempotency_key": idempotency_key,
        "operation_id": operation_id,
        "policy_decision": policy_decision.value,
        "policy_source": policy_source,
        "policy_actor": policy_actor,
        "policy_evidence_sha256": policy_evidence_sha256,
        "issued_at": _utc(issued),
        "expires_at": _utc(issued + timedelta(seconds=ttl_seconds)),
        "external_reference": external_reference,
        "reconciliation_method": reconciliation_method,
        "previous_receipt_sha256": previous_receipt_sha256,
        "provenance_provider": provenance_provider,
        "provenance_authority": provenance_authority,
        "provenance_subject": provenance_subject,
        "provenance_evidence_sha256": provenance_evidence_sha256,
        "provenance_authenticated": provenance_authenticated,
        "signer_authority": "lightwork-audit-ed25519",
    }
    # Validate all user/provider-controlled fields before touching the signer.
    unsigned_probe = {
        **body,
        "payload_sha256": ZERO_SHA256,
        "signer_key_id": "0" * 16,
        "signature": "0" * 128,
        "receipt_sha256": ZERO_SHA256,
    }
    probe = ConsequentialReceipt.from_mapping(unsigned_probe)
    _validate_receipt_shape(probe, now=issued)
    payload_digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()

    from .audit.signing import _load_or_create_keypair

    private_bytes, public_bytes, key_id = _load_or_create_keypair()
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - declared core dependency
        raise ConsequentialReceiptError(
            "cryptography is required for consequential receipts"
        ) from exc
    signer = ed25519.Ed25519PrivateKey.from_private_bytes(private_bytes)
    derived_public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if derived_public != public_bytes:
        raise ConsequentialReceiptError("consequential receipt signer is inconsistent")
    envelope = {
        **body,
        "payload_sha256": payload_digest,
        "signer_key_id": key_id,
        "signature": signer.sign(bytes.fromhex(payload_digest)).hex(),
    }
    record = {
        **envelope,
        "receipt_sha256": hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest(),
    }
    receipt = ConsequentialReceipt.from_mapping(record)
    _validate_receipt_shape(receipt, now=issued)
    return receipt


def verify_receipt(
    value: ConsequentialReceipt | Mapping[str, Any],
    *,
    expectation: ReceiptExpectation,
    now: datetime | None = None,
) -> ConsequentialReceipt:
    """Authenticate a receipt and bind it to one exact production dispatch."""
    receipt = (
        value
        if isinstance(value, ConsequentialReceipt)
        else ConsequentialReceipt.from_mapping(value)
    )
    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _validate_receipt_shape(receipt, now=checked_at)
    record = receipt.as_dict()
    body = {field: record[field] for field in _BODY_FIELDS}
    payload_digest = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(payload_digest, receipt.payload_sha256):
        raise ConsequentialReceiptError("receipt payload digest is invalid")
    envelope = {key: record[key] for key in _SIGNED_FIELDS - {"receipt_sha256"}}
    receipt_digest = hashlib.sha256(_canonical(envelope).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(receipt_digest, receipt.receipt_sha256):
        raise ConsequentialReceiptError("receipt envelope digest is invalid")

    from .audit.signing import trusted_audit_public_keys, verify_ed25519

    try:
        public_keys = trusted_audit_public_keys()
    except Exception as exc:
        raise ConsequentialReceiptError("receipt trust registry is unavailable") from exc
    public_key = public_keys.get(receipt.signer_key_id)
    if not public_key or not verify_ed25519(
        public_key,
        receipt.signature,
        bytes.fromhex(payload_digest),
    ):
        raise ConsequentialReceiptError("receipt signature is invalid")
    exact = {
        "tenant_id": expectation.tenant_id,
        "principal_id": expectation.principal_id,
        "tool_name": expectation.tool_name,
        "action": expectation.action,
        "target": expectation.target,
        "request_sha256": expectation.request_sha256,
        "idempotency_key": expectation.idempotency_key,
        "operation_id": expectation.operation_id,
        "reconciliation_method": expectation.reconciliation_method,
    }
    for field, expected in exact.items():
        if not hmac.compare_digest(str(getattr(receipt, field)), str(expected)):
            raise ConsequentialReceiptError(f"receipt {field} mismatches dispatch")
    if receipt.state not in expectation.allowed_states:
        raise ConsequentialReceiptError("receipt state is not allowed here")
    if expectation.previous_receipt_sha256 is not None and not hmac.compare_digest(
        receipt.previous_receipt_sha256,
        expectation.previous_receipt_sha256,
    ):
        raise ConsequentialReceiptError("receipt previous_receipt_sha256 mismatches dispatch")
    return receipt


class ReceiptReplayGuard:
    """Durable, cross-process one-use claims for authenticated receipts."""

    def __init__(self, root: str | Path | None = None):
        self._root = Path(root) if root is not None else None

    def _claim_path(self, *, tenant_id: str, receipt_sha256: str) -> Path:
        if not _SHA256_RE.fullmatch(receipt_sha256):
            raise ConsequentialReceiptError("receipt claim digest is invalid")
        tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        if self._root is None:
            from .paths import data_dir

            root = data_dir("consequential-receipt-claims", tenant=None)
        else:
            root = self._root
        return (
            root
            / tenant_hash[:2]
            / tenant_hash[:32]
            / f"{receipt_sha256[:32]}.json"
        )

    def require_consumed(
        self,
        receipt_sha256: str,
        *,
        tenant_id: str,
        operation_id: str,
        state: ReceiptState,
    ) -> None:
        """Require an exact immutable predecessor claim without creating it."""
        path = self._claim_path(
            tenant_id=tenant_id,
            receipt_sha256=receipt_sha256,
        )
        from .file_lock import atomic_read_text, ensure_private_file

        try:
            ensure_private_file(path)
            raw = atomic_read_text(path)
            if len(raw.encode("utf-8")) > 4096:
                raise ValueError("claim is too large")
            claim = json.loads(raw)
        except (OSError, TypeError, ValueError) as exc:
            raise ConsequentialReceiptError(
                "receipt predecessor was not durably consumed"
            ) from exc
        tenant_hash = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        expected = {
            "schema": CLAIM_SCHEMA,
            "receipt_sha256": receipt_sha256,
            "operation_id": operation_id,
            "state": state.value,
            "tenant_sha256": tenant_hash,
        }
        if claim != expected:
            raise ConsequentialReceiptError(
                "receipt predecessor claim mismatches the final receipt"
            )

    def consume(
        self,
        value: ConsequentialReceipt | Mapping[str, Any],
        *,
        expectation: ReceiptExpectation,
        now: datetime | None = None,
    ) -> ConsequentialReceipt:
        receipt = verify_receipt(value, expectation=expectation, now=now)
        tenant_hash = hashlib.sha256(receipt.tenant_id.encode("utf-8")).hexdigest()
        claim_path = self._claim_path(
            tenant_id=receipt.tenant_id,
            receipt_sha256=receipt.receipt_sha256,
        )
        claim_dir = claim_path.parent
        root = claim_dir.parent.parent
        from .file_lock import atomic_create_text, ensure_private_directory

        ensure_private_directory(root)
        ensure_private_directory(root / tenant_hash[:2])
        ensure_private_directory(claim_dir)
        claim = {
            "schema": CLAIM_SCHEMA,
            "receipt_sha256": receipt.receipt_sha256,
            "operation_id": receipt.operation_id,
            "state": receipt.state.value,
            "tenant_sha256": tenant_hash,
        }
        try:
            atomic_create_text(
                claim_path,
                _canonical(claim) + "\n",
            )
        except FileExistsError as exc:
            raise ReceiptReplayError("consequential receipt was already consumed") from exc
        return receipt


def new_operation_id() -> str:
    return uuid.uuid4().hex


def receipt_display(receipt: ConsequentialReceipt, detail: str = "") -> str:
    safe = str(detail or "").replace("\r", " ").replace("\n", " ")[:1000]
    suffix = f"; {safe}" if safe else ""
    return (
        f"{receipt.state.value} (typed receipt): operation_id={receipt.operation_id}; "
        f"receipt_sha256={receipt.receipt_sha256}{suffix}"
    )


__all__ = [
    "CLAIM_SCHEMA",
    "DEFAULT_RECEIPT_TTL_SECONDS",
    "MAX_FUTURE_SKEW_SECONDS",
    "MAX_RECEIPT_TTL_SECONDS",
    "PolicyDecision",
    "ProviderMutationReceipt",
    "RECEIPT_SCHEMA",
    "ReceiptExpectation",
    "ReceiptReplayError",
    "ReceiptReplayGuard",
    "ReceiptState",
    "ConsequentialReceipt",
    "ConsequentialReceiptError",
    "ConsequentialToolResult",
    "ZERO_SHA256",
    "canonical_request",
    "derive_idempotency_key",
    "evidence_sha256",
    "issue_receipt",
    "new_operation_id",
    "receipt_action",
    "receipt_display",
    "receipt_target",
    "request_sha256",
    "verify_receipt",
]
