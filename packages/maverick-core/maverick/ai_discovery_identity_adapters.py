"""Read-only, privacy-bounded identity-audit discovery adapters.

Okta System Log, Microsoft Entra audit/sign-in, and Google Workspace Reports
are useful *signals* for shadow-AI discovery, but their source records often
contain people, client identifiers, IP addresses, tokens, and arbitrary
provider payloads.  This module never stores or returns those records.  It
matches a source application only through a reviewed SHA-256 binding, then
emits the existing closed :class:`NormalizedSensorEvent` shape.

The adapters deliberately do not make HTTP calls or hold credentials.  A
deployment-owned, read-only collector supplies bounded event records in
memory; :meth:`IdentityAuditAdapter.ingest` immediately sends only normalized
events into the existing consent-gated sensor agent spool and signed transport.
This separation prevents a convenience adapter from becoming an unbounded
directory crawler or a second durable store of identity telemetry.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .ai_discovery_sensor_clients import (
    DiscoverySensorClientError,
    NormalizedSensorEvent,
    normalize_identity_audit,
)

IDENTITY_AUDIT_SOURCES = frozenset(
    {
        "okta_system_log",
        "entra_signin",
        "entra_consent",
        "google_workspace_login",
        "google_workspace_token",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_EVENTS = 500
_MAX_SOURCE_REF_BYTES = 512
_SAFE_CONTEXT_CODES = frozenset(
    {
        "admin_consent",
        "approved_source",
        "corporate_network",
        "delegated",
        "interactive",
        "managed_device",
        "non_interactive",
        "service_principal",
        "test_fixture",
        "user_consent",
    }
)
_SAFE_PRODUCTS = frozenset(
    {
        ("anthropic", "claude"),
        ("google", "gemini"),
        ("hugging_face", "hugging_face_hub"),
        ("microsoft", "copilot"),
        ("openai", "chatgpt"),
        ("openai", "openai_api"),
    }
)
_SAFE_ENVIRONMENTS = frozenset(
    {"consenting_test", "development", "production", "sandbox", "staging", "test", "workforce"}
)
_DEPLOYMENT_TYPES = frozenset(
    {"embedded", "hosted_api", "managed_cloud", "saas", "self_hosted", "unknown"}
)


class IdentityAuditAdapterError(ValueError):
    """A source record or reviewed application binding is unsafe or malformed."""


class IdentityAuditAdapterLimitError(IdentityAuditAdapterError):
    """A caller supplied more source records than the adapter can bound."""


def _token(value: object, label: str) -> str:
    text = str(value or "").strip().lower()
    if not _TOKEN_RE.fullmatch(text):
        raise IdentityAuditAdapterError(f"{label} is invalid")
    return text


def _digest(value: object, label: str) -> str:
    text = str(value or "").strip().lower().removeprefix("sha256:")
    if not _DIGEST_RE.fullmatch(text):
        raise IdentityAuditAdapterError(f"{label} must be a SHA-256 digest")
    return text


def _source_ref(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise IdentityAuditAdapterError(f"{label} is invalid")
    text = value.strip()
    if (
        not text
        or len(text.encode("utf-8", "replace")) > _MAX_SOURCE_REF_BYTES
        or any(ord(character) < 32 for character in text)
    ):
        raise IdentityAuditAdapterError(f"{label} is invalid")
    return text


def _binding_salt(value: object) -> bytes:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    elif isinstance(value, bytes):
        encoded = bytes(value)
    else:
        raise IdentityAuditAdapterError("binding salt is invalid")
    if not 16 <= len(encoded) <= 4_096:
        raise IdentityAuditAdapterError("binding salt is invalid")
    return encoded


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IdentityAuditAdapterError(f"{label} is invalid")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise IdentityAuditAdapterError(f"{label} is invalid")
    if len(value) > 128:
        raise IdentityAuditAdapterError(f"{label} exceeds the closed bound")
    return value


def _timestamp(value: object) -> float:
    if isinstance(value, bool):
        raise IdentityAuditAdapterError("source timestamp is invalid")
    if isinstance(value, (int, float)):
        parsed = float(value)
        if math.isfinite(parsed) and parsed > 0:
            return parsed
        raise IdentityAuditAdapterError("source timestamp is invalid")
    text = _source_ref(value, "source timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IdentityAuditAdapterError("source timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise IdentityAuditAdapterError("source timestamp must include a timezone")
    result = parsed.astimezone(timezone.utc).timestamp()
    if not math.isfinite(result) or result <= 0:
        raise IdentityAuditAdapterError("source timestamp is invalid")
    return result


def _contexts(values: Iterable[object]) -> tuple[str, ...]:
    result = tuple(sorted({_token(value, "context code") for value in values}))
    if len(result) > 16 or any(value not in _SAFE_CONTEXT_CODES for value in result):
        raise IdentityAuditAdapterError("context code is outside the reviewed taxonomy")
    return result


def source_application_sha256(
    source: str, application_ref: str, *, binding_salt: str | bytes
) -> str:
    """Derive the non-reversible reviewed-binding key for a source app/client.

    Call this at review time with a provider's application/client identifier
    and a deployment-held secret salt. Persist only the returned HMAC digest in
    :class:`ReviewedAIApplication`; the raw identifier and salt are never
    accepted by the durable discovery sensor path.
    """

    source_code = _token(source, "identity audit source")
    if source_code not in IDENTITY_AUDIT_SOURCES:
        raise IdentityAuditAdapterError("identity audit source is unsupported")
    raw = _source_ref(application_ref, "source application reference")
    return hmac.new(
        _binding_salt(binding_salt),
        b"lightwork.identity-audit-application.v1\0"
        + source_code.encode("utf-8")
        + b"\0"
        + raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _source_event_sha256(
    source: str, event_ref: str, application_digest: str, *, binding_salt: bytes
) -> str:
    return hmac.new(
        binding_salt,
        b"lightwork.identity-audit-event.v1\0"
        + source.encode("utf-8")
        + b"\0"
        + _source_ref(event_ref, "source event reference").encode("utf-8")
        + b"\0"
        + application_digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class ReviewedAIApplication:
    """One reviewed mapping from a source application digest to AI taxonomy."""

    source: str
    source_application_sha256: str
    ai_service_code: str
    ai_product_code: str
    provider_code: str

    def __post_init__(self) -> None:
        source = _token(self.source, "identity audit source")
        if source not in IDENTITY_AUDIT_SOURCES:
            raise IdentityAuditAdapterError("identity audit source is unsupported")
        application_digest = _digest(
            self.source_application_sha256, "source application digest"
        )
        service = _token(self.ai_service_code, "ai_service_code")
        product = _token(self.ai_product_code, "ai_product_code")
        provider = _token(self.provider_code, "provider_code")
        if service != provider or (provider, product) not in _SAFE_PRODUCTS:
            raise IdentityAuditAdapterError("AI product is outside the reviewed taxonomy")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "source_application_sha256", application_digest)
        object.__setattr__(self, "ai_service_code", service)
        object.__setattr__(self, "ai_product_code", product)
        object.__setattr__(self, "provider_code", provider)


class IdentityAuditAdapter:
    """Base parser for one bounded, read-only identity-audit source.

    Subclasses inspect a provider record only in the call frame.  A source app
    must exactly match a reviewed digest binding before any event is emitted.
    Unknown, malformed, unsuccessful, or ambiguous records return ``None`` or
    raise a sanitized validation error; none are retained.
    """

    source: str

    def __init__(
        self,
        bindings: Iterable[ReviewedAIApplication],
        *,
        environment_code: str,
        deployment_type: str = "saas",
        binding_salt: str | bytes,
    ) -> None:
        source = _token(getattr(self, "source", ""), "identity audit source")
        if source not in IDENTITY_AUDIT_SOURCES:
            raise IdentityAuditAdapterError("identity audit source is unsupported")
        environment = _token(environment_code, "environment_code")
        deployment = _token(deployment_type, "deployment_type")
        if environment not in _SAFE_ENVIRONMENTS:
            raise IdentityAuditAdapterError("environment_code is outside the reviewed taxonomy")
        if deployment not in _DEPLOYMENT_TYPES:
            raise IdentityAuditAdapterError("deployment_type is unsupported")
        reviewed: dict[str, ReviewedAIApplication] = {}
        for binding in bindings:
            if not isinstance(binding, ReviewedAIApplication) or binding.source != source:
                raise IdentityAuditAdapterError("reviewed application binding is invalid")
            if binding.source_application_sha256 in reviewed:
                raise IdentityAuditAdapterError("reviewed application bindings are duplicated")
            reviewed[binding.source_application_sha256] = binding
        if not reviewed:
            raise IdentityAuditAdapterError("at least one reviewed application binding is required")
        self._bindings = reviewed
        self._environment_code = environment
        self._deployment_type = deployment
        self._binding_salt = _binding_salt(binding_salt)

    @property
    def source_code(self) -> str:
        return self.source

    def _binding_for_refs(
        self, raw_application_refs: Iterable[object]
    ) -> tuple[str, ReviewedAIApplication] | None:
        matches: list[tuple[str, ReviewedAIApplication]] = []
        seen: set[str] = set()
        for raw_ref in raw_application_refs:
            digest = source_application_sha256(
                self.source,
                _source_ref(raw_ref, "source application reference"),
                binding_salt=self._binding_salt,
            )
            if digest in seen:
                continue
            seen.add(digest)
            binding = self._bindings.get(digest)
            if binding is not None:
                matches.append((digest, binding))
        if not matches:
            return None
        if len(matches) != 1:
            raise IdentityAuditAdapterError("source record has ambiguous reviewed applications")
        return matches[0]

    def _candidate(self, record: Mapping[str, Any]) -> tuple[str, float, tuple[object, ...], tuple[str, ...]] | None:
        raise NotImplementedError

    def normalize(
        self,
        record: object,
        *,
        base_context_codes: Iterable[object] = (),
    ) -> NormalizedSensorEvent | None:
        row = _mapping(record, "identity audit record")
        candidate = self._candidate(row)
        if candidate is None:
            return None
        event_ref, observed_at, raw_application_refs, source_contexts = candidate
        matched = self._binding_for_refs(raw_application_refs)
        if matched is None:
            return None
        application_digest, binding = matched
        contexts = _contexts((*base_context_codes, *source_contexts))
        try:
            return normalize_identity_audit(
                source_event_sha256=_source_event_sha256(
                    self.source,
                    event_ref,
                    application_digest,
                    binding_salt=self._binding_salt,
                ),
                observed_at=observed_at,
                ai_service_code=binding.ai_service_code,
                ai_product_code=binding.ai_product_code,
                provider_code=binding.provider_code,
                environment_code=self._environment_code,
                deployment_type=self._deployment_type,
                context_codes=contexts,
            )
        except DiscoverySensorClientError as exc:
            raise IdentityAuditAdapterError("identity audit event failed normalization") from exc

    def ingest(self, records: Iterable[object], sensor_agent: object) -> dict[str, int]:
        """Normalize a bounded page and queue only closed events in ``sensor_agent``.

        ``sensor_agent`` must be a consent-gated ``DiscoverySensorAgent`` with
        ``source_kind = identity_directory``.  Its existing private SQLite
        spool, rotating Ed25519 signer, and signed HTTPS delivery route are the
        only durable/egress path used here.
        """

        from .ai_discovery_sensor_agents import DiscoverySensorAgent

        if not isinstance(sensor_agent, DiscoverySensorAgent):
            raise TypeError("sensor_agent must be a DiscoverySensorAgent")
        if sensor_agent.config.source_kind != "identity_directory":
            raise IdentityAuditAdapterError(
                "identity audit adapters require an identity-directory sensor agent"
            )
        normalized: list[NormalizedSensorEvent | None] = []
        input_events = 0
        invalid_events = 0
        for record in records:
            input_events += 1
            if input_events > _MAX_EVENTS:
                raise IdentityAuditAdapterLimitError("identity audit page exceeds the event bound")
            try:
                normalized.append(
                    self.normalize(
                        record,
                        base_context_codes=sensor_agent.config.context_codes,
                    )
                )
            except IdentityAuditAdapterError:
                invalid_events += 1
        queued = sensor_agent.ingest_normalized(normalized)
        return {
            "input": input_events,
            "normalized": queued["normalized"],
            "unmatched": queued["unmatched"],
            "invalid": invalid_events + queued["invalid"],
            "duplicate": queued["duplicate"],
        }


class OktaSystemLogDiscoveryAdapter(IdentityAuditAdapter):
    """Normalize successful Okta System Log SSO/OAuth events for reviewed apps."""

    source = "okta_system_log"
    _EVENT_TYPES = frozenset(
        {
            "app.oauth2.as.authorize.grant",
            "application.authorization.grant",
            "user.authentication.sso",
        }
    )

    def _candidate(self, record: Mapping[str, Any]) -> tuple[str, float, tuple[object, ...], tuple[str, ...]] | None:
        event_type = _source_ref(record.get("eventType"), "Okta event type").lower()
        if event_type not in self._EVENT_TYPES:
            return None
        outcome = _mapping(record.get("outcome"), "Okta outcome")
        if _source_ref(outcome.get("result"), "Okta outcome result").upper() != "SUCCESS":
            return None
        targets = _sequence(record.get("target"), "Okta targets")
        refs: list[object] = []
        for target in targets:
            item = _mapping(target, "Okta target")
            target_type = _source_ref(item.get("type"), "Okta target type").lower()
            if target_type in {"appinstance", "application", "oauthclient", "client"}:
                refs.append(item.get("id"))
        if not refs:
            return None
        contexts = ("delegated", "user_consent") if "grant" in event_type else ("interactive",)
        return (
            _source_ref(record.get("uuid"), "Okta event id"),
            _timestamp(record.get("published")),
            tuple(refs),
            contexts,
        )


class EntraSignInDiscoveryAdapter(IdentityAuditAdapter):
    """Normalize successful Entra sign-in records for reviewed applications."""

    source = "entra_signin"

    def _candidate(self, record: Mapping[str, Any]) -> tuple[str, float, tuple[object, ...], tuple[str, ...]] | None:
        status = _mapping(record.get("status"), "Entra sign-in status")
        if status.get("errorCode") != 0:
            return None
        app_ref = record.get("appId") or record.get("resourceServicePrincipalId")
        if not app_ref:
            return None
        raw_types = record.get("signInEventTypes", [])
        event_types = {
            _source_ref(value, "Entra sign-in event type").lower()
            for value in _sequence(raw_types, "Entra sign-in event types")
        }
        if "serviceprincipal" in event_types:
            contexts = ("service_principal", "non_interactive")
        elif "noninteractiveuser" in event_types:
            contexts = ("non_interactive",)
        else:
            contexts = ("interactive",)
        return (
            _source_ref(record.get("id"), "Entra sign-in id"),
            _timestamp(record.get("createdDateTime")),
            (app_ref,),
            contexts,
        )


class EntraConsentDiscoveryAdapter(IdentityAuditAdapter):
    """Normalize Entra consent-audit records for reviewed applications."""

    source = "entra_consent"

    def _candidate(self, record: Mapping[str, Any]) -> tuple[str, float, tuple[object, ...], tuple[str, ...]] | None:
        activity = _source_ref(
            record.get("activityDisplayName"), "Entra consent activity"
        ).lower()
        if "consent" not in activity and "permission grant" not in activity:
            return None
        targets = _sequence(record.get("targetResources"), "Entra consent targets")
        refs: list[object] = []
        for target in targets:
            item = _mapping(target, "Entra consent target")
            for key in ("appId", "id"):
                if item.get(key):
                    refs.append(item[key])
        if not refs:
            return None
        contexts = ("admin_consent", "delegated") if "admin" in activity else ("delegated", "user_consent")
        return (
            _source_ref(record.get("id"), "Entra consent id"),
            _timestamp(record.get("activityDateTime")),
            tuple(refs),
            contexts,
        )


class _GoogleWorkspaceDiscoveryAdapter(IdentityAuditAdapter):
    _APPLICATION_NAME: str
    _CONTEXTS: tuple[str, ...]

    def _candidate(self, record: Mapping[str, Any]) -> tuple[str, float, tuple[object, ...], tuple[str, ...]] | None:
        identifier = _mapping(record.get("id"), "Google Workspace event id")
        if _source_ref(identifier.get("applicationName"), "Google Workspace application name").lower() != self._APPLICATION_NAME:
            return None
        event_ref = _source_ref(identifier.get("uniqueQualifier"), "Google Workspace event id")
        observed = _timestamp(identifier.get("time"))
        events = _sequence(record.get("events"), "Google Workspace events")
        refs: list[object] = []
        for event in events:
            item = _mapping(event, "Google Workspace event")
            parameters = _sequence(item.get("parameters", []), "Google Workspace event parameters")
            for parameter in parameters:
                value = _mapping(parameter, "Google Workspace event parameter")
                name = _source_ref(value.get("name"), "Google Workspace parameter name").lower()
                if name in {"application_id", "client_id", "oauth_client_id"} and value.get("value"):
                    refs.append(value["value"])
        if not refs:
            return None
        return event_ref, observed, tuple(refs), self._CONTEXTS


class GoogleWorkspaceLoginDiscoveryAdapter(_GoogleWorkspaceDiscoveryAdapter):
    """Normalize Google Workspace Admin Reports login records for reviewed apps."""

    source = "google_workspace_login"
    _APPLICATION_NAME = "login"
    _CONTEXTS = ("interactive",)


class GoogleWorkspaceTokenDiscoveryAdapter(_GoogleWorkspaceDiscoveryAdapter):
    """Normalize Google Workspace Admin Reports token-audit records for reviewed apps."""

    source = "google_workspace_token"
    _APPLICATION_NAME = "token"
    _CONTEXTS = ("delegated", "user_consent")


__all__ = [
    "EntraConsentDiscoveryAdapter",
    "EntraSignInDiscoveryAdapter",
    "GoogleWorkspaceLoginDiscoveryAdapter",
    "GoogleWorkspaceTokenDiscoveryAdapter",
    "IDENTITY_AUDIT_SOURCES",
    "IdentityAuditAdapter",
    "IdentityAuditAdapterError",
    "IdentityAuditAdapterLimitError",
    "OktaSystemLogDiscoveryAdapter",
    "ReviewedAIApplication",
    "source_application_sha256",
]
