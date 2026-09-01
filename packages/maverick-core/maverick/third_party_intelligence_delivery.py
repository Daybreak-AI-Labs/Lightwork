"""Explicit notification worker for third-party monitoring alerts.

The intelligence authority owns a durable at-least-once outbox while this
module owns the external side effect. Deployments inject an opaque destination
resolver and transport; credentials and resolved addresses never enter the
third-party intelligence store or returned batch evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Protocol

from .third_party_intelligence import OutboxItem, ThirdPartyIntelligenceStore


class ThirdPartyAlertDeliveryError(RuntimeError):
    """Base error for the external notification boundary."""


class DestinationResolutionError(ThirdPartyAlertDeliveryError):
    """An opaque destination reference could not be safely resolved."""


class ThirdPartyAlertTransport(Protocol):
    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> None: ...


DestinationResolver = Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    delivery_id: str
    state: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryBatchResult:
    tenant_id: str
    claimed: int
    acknowledged: int
    retry_pending: int
    dead_lettered: int
    acknowledgement_ambiguous: int
    outcomes: tuple[DeliveryOutcome, ...]
    semantics: str = "at_least_once"


class SmtpThirdPartyAlertTransport:
    """Use Lightwork's credential-safe platform mail sender."""

    def send(
        self,
        *,
        recipient: str,
        subject: str,
        body: str,
        idempotency_key: str,
    ) -> None:
        from . import mailer

        mailer.send(
            recipient,
            subject,
            f"{body}\n\nLightwork delivery reference: {idempotency_key}\n",
        )


def _recipient(value: str) -> str:
    if not isinstance(value, str):
        raise DestinationResolutionError("destination resolver returned non-text")
    candidate = value.strip()
    if not candidate or "\r" in candidate or "\n" in candidate:
        raise DestinationResolutionError("destination resolver returned unsafe value")
    display, address = parseaddr(candidate)
    if display or address != candidate or len(address) > 320:
        raise DestinationResolutionError("destination must be one plain email address")
    local, separator, domain = address.rpartition("@")
    if not separator or not local or not domain or "." not in domain:
        raise DestinationResolutionError("destination is not a valid email address")
    return address


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, DestinationResolutionError):
        return "destination_unavailable"
    name = type(error).__name__.lower()
    if "reject" in name or "refus" in name:
        return "provider_rejected"
    if "timeout" in name or "connection" in name or "transport" in name:
        return "transport_unavailable"
    return "delivery_error"


def _message(store: ThirdPartyIntelligenceStore, item: OutboxItem) -> str:
    alert = store.get_monitoring_alert(item.tenant_id, item.alert_id)
    monitor = store.get_monitor(item.tenant_id, alert.monitor_id)
    return (
        "A governed third-party monitoring observation changed and requires "
        "review.\n\n"
        f"Vendor reference: {alert.vendor_id}\n"
        f"Monitor reference: {monitor.monitor_id}\n"
        f"Alert reference: {alert.alert_id}\n"
        f"Previous observation SHA-256: {alert.previous_observation_sha256}\n"
        f"Current observation SHA-256: {alert.observation_sha256}\n"
        f"Evidence SHA-256: {alert.evidence_sha256}\n\n"
        "Open the authenticated Third-Party Intelligence workspace to review "
        "the licensed source citations and decide the response. This alert is "
        "not a sanctions, legal, compliance, or vendor-approval conclusion."
    )


def deliver_alert_batch(
    store: ThirdPartyIntelligenceStore,
    *,
    tenant_id: str,
    worker_id: str,
    resolve_destination: DestinationResolver,
    transport: ThirdPartyAlertTransport,
    limit: int = 10,
    lease_seconds: float = 60.0,
    base_backoff_seconds: float = 30.0,
    maximum_backoff_seconds: float = 86_400.0,
) -> DeliveryBatchResult:
    """Claim and deliver one bounded batch with explicit at-least-once limits."""

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if not isinstance(lease_seconds, (int, float)) or isinstance(lease_seconds, bool):
        raise ValueError("lease_seconds must be numeric")
    if not 1.0 <= float(lease_seconds) <= 3_600.0:
        raise ValueError("lease_seconds must be from 1 through 3600")
    if not callable(resolve_destination):
        raise TypeError("resolve_destination must be callable")

    items = store.claim_outbox(
        tenant_id,
        worker_id=worker_id,
        limit=limit,
        lease_seconds=float(lease_seconds),
    )
    outcomes: list[DeliveryOutcome] = []
    acknowledged = retry_pending = dead_lettered = ambiguous = 0
    for item in items:
        assert item.lease_token is not None
        try:
            recipient = _recipient(
                resolve_destination(item.tenant_id, item.destination_ref)
            )
            transport.send(
                recipient=recipient,
                subject="Lightwork third-party risk change",
                body=_message(store, item),
                idempotency_key=item.delivery_id,
            )
        except Exception as error:  # noqa: BLE001 - sanitized at transport boundary
            error_code = _safe_error_code(error)
            backoff = min(
                float(maximum_backoff_seconds),
                float(base_backoff_seconds) * (2 ** max(0, item.attempt - 1)),
            )
            failed = store.fail_outbox(
                item.tenant_id,
                item.delivery_id,
                lease_token=item.lease_token,
                error_code=error_code,
                backoff_seconds=backoff,
            )
            if failed.state == "dead_letter":
                dead_lettered += 1
            else:
                retry_pending += 1
            outcomes.append(DeliveryOutcome(item.delivery_id, failed.state, error_code))
            continue

        try:
            stored = store.acknowledge_outbox(
                item.tenant_id,
                item.delivery_id,
                lease_token=item.lease_token,
            )
        except Exception:  # noqa: BLE001 - send succeeded; outcome is ambiguous
            ambiguous += 1
            outcomes.append(
                DeliveryOutcome(item.delivery_id, "ack_ambiguous", "acknowledgement_error")
            )
        else:
            acknowledged += 1
            outcomes.append(DeliveryOutcome(item.delivery_id, stored.state))

    return DeliveryBatchResult(
        tenant_id=tenant_id,
        claimed=len(items),
        acknowledged=acknowledged,
        retry_pending=retry_pending,
        dead_lettered=dead_lettered,
        acknowledgement_ambiguous=ambiguous,
        outcomes=tuple(outcomes),
    )


__all__ = [
    "DeliveryBatchResult",
    "DeliveryOutcome",
    "DestinationResolutionError",
    "SmtpThirdPartyAlertTransport",
    "ThirdPartyAlertDeliveryError",
    "ThirdPartyAlertTransport",
    "deliver_alert_batch",
]
