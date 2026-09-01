"""Fixed-authority provider transport boundary for identity governance.

Provider-specific URLs, raw identifiers, credentials, and response parsing stay
inside an independently installed deployment authority.  This module enforces
the cross-provider invariants that Lightwork can establish without inventing a
Microsoft, Okta, or Purview wire contract: exact tenant/provider binding, exact
HTTPS hosts, redirect refusal, bounded requests and responses, retry limits,
write-idempotency proof, and read-after-write ambiguity recovery.

The boundary does not claim that a provider endpoint or sandbox has accepted an
operation.  A deployment must supply and accept the provider-specific authority.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from .ai_identity_governance_connectors import BoundedHTTPResponse
from .ai_identity_governance_controls import (
    AgentPrincipalFederationObservation,
    ConditionalAccessObservation,
    ConditionalAccessObservationRequest,
    FederationObservationRequest,
    IdentityGovernanceBindingError,
    IdentityGovernanceControlConfigError,
    IdentityGovernanceDeliveryError,
    PurviewWritebackAck,
    PurviewWritebackRequest,
)

Provider = Literal["entra", "okta", "purview"]
ObservationPurpose = Literal["federation", "conditional_access"]
ProviderOperation = Literal[
    "federation_observe",
    "conditional_access_observe",
    "purview_apply",
    "purview_compensate",
]

PROVIDER_OPERATION_SCHEMA = "lightwork.identity_governance.provider-operation.v1"
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_PROVIDERS = frozenset({"entra", "okta", "purview"})
_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_HEADER_BYTES = 32_768
_PSEUDONYM_RE = re.compile(r"(?:prn|sub|idn)_[0-9a-f]{32}\Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a SHA-256 digest")
    selected = value.strip().lower().removeprefix("sha256:")
    if not _DIGEST_RE.fullmatch(selected):
        raise ValueError(f"{label} must be a SHA-256 digest")
    return selected


def _provider(value: object) -> str:
    selected = str(value or "").strip().lower()
    if selected not in _PROVIDERS:
        raise ValueError("provider wire authority is unsupported")
    return selected


def _positive_int(value: object, label: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ValueError(f"{label} is invalid")
    return value


def _positive_float(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not minimum <= float(value) <= maximum
    ):
        raise ValueError(f"{label} is invalid")
    return float(value)


def _host(value: object) -> str:
    selected = str(value or "").strip().lower().rstrip(".")
    if not _HOST_RE.fullmatch(selected):
        raise ValueError("provider fixed host is invalid")
    return selected


def _request_sha256(request: object) -> str:
    try:
        return _sha256(asdict(request))
    except (TypeError, ValueError) as exc:
        raise IdentityGovernanceBindingError(
            "provider_wire_request_invalid"
        ) from exc


def _validate_observation_request(
    request: FederationObservationRequest | ConditionalAccessObservationRequest,
    *,
    purpose: ObservationPurpose,
) -> None:
    expected_schema = (
        "lightwork.identity_governance.agent-principal-query.v1"
        if purpose == "federation"
        else "lightwork.identity_governance.conditional-access-query.v1"
    )
    try:
        tenant = _digest(request.tenant_sha256, "tenant_sha256")
        binding = _digest(request.binding_sha256, "binding_sha256")
        revision = _positive_int(
            request.binding_revision,
            "binding_revision",
            maximum=2**63 - 1,
        )
    except (TypeError, ValueError) as exc:
        raise IdentityGovernanceBindingError(
            "provider_observation_request_invalid"
        ) from exc
    if (
        request.schema != expected_schema
        or tenant != request.tenant_sha256
        or binding != request.binding_sha256
        or revision != request.binding_revision
        or not _PSEUDONYM_RE.fullmatch(request.principal_ref)
        or not _PSEUDONYM_RE.fullmatch(request.provider_subject_id)
        or not request.principal_ref.startswith("prn_")
        or request.provider_subject_id.startswith("prn_")
    ):
        raise IdentityGovernanceBindingError(
            "provider_observation_request_invalid"
        )


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 64:
        raise ValueError("provider operation headers are invalid")
    selected: dict[str, str] = {}
    total = 0
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValueError("provider operation headers are invalid")
        name = raw_name.strip()
        lowered = name.lower()
        if (
            not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}", name)
            or lowered in {"host", "proxy-authorization", "content-length"}
            or "\r" in raw_value
            or "\n" in raw_value
            or len(raw_value) > 8_192
        ):
            raise ValueError("provider operation headers are invalid")
        total += len(name.encode("ascii")) + len(raw_value.encode("utf-8"))
        if total > _MAX_HEADER_BYTES:
            raise ValueError("provider operation headers are too large")
        if lowered in {item.lower() for item in selected}:
            raise ValueError("provider operation header names must be unique")
        selected[name] = raw_value
    return selected


def _body(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("provider operation body is invalid")
    try:
        encoded = _canonical(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("provider operation body is invalid") from exc
    if len(encoded) > _MAX_REQUEST_BYTES:
        raise ValueError("provider operation body is too large")
    return dict(value)


def _fixed_url(value: object, fixed_hosts: frozenset[str]) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("provider operation URL is invalid")
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or hostname not in fixed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("provider operation URL is outside fixed authority")
    return value


@dataclass(frozen=True, repr=False)
class FixedProviderOperation:
    """One ephemeral provider operation issued by a credential authority."""

    schema: str
    tenant_sha256: str
    provider: Provider | str
    operation: ProviderOperation | str
    method: str
    url: str
    headers: Mapping[str, str]
    json_body: Mapping[str, object] | None
    request_sha256: str
    authority_revision: int
    authority_sha256: str
    timeout_seconds: float
    max_response_bytes: int
    retry_attempts: int
    retry_backoff_seconds: float
    idempotency_header_name: str | None
    operation_sha256: str

    def __post_init__(self) -> None:
        if self.schema != PROVIDER_OPERATION_SCHEMA:
            raise ValueError("provider operation schema is unsupported")
        tenant = _digest(self.tenant_sha256, "tenant_sha256")
        provider = _provider(self.provider)
        operation = str(self.operation or "").strip().lower()
        expected_prefix = "purview" if provider == "purview" else (
            "federation" if operation == "federation_observe" else "conditional_access"
        )
        if operation not in {
            "federation_observe",
            "conditional_access_observe",
            "purview_apply",
            "purview_compensate",
        } or not operation.startswith(expected_prefix):
            raise ValueError("provider operation kind is invalid")
        method = str(self.method or "").strip().upper()
        if method not in _METHODS:
            raise ValueError("provider operation method is unsupported")
        headers = _headers(self.headers)
        body = _body(self.json_body)
        request_sha = _digest(self.request_sha256, "request_sha256")
        authority_revision = _positive_int(
            self.authority_revision,
            "authority_revision",
            maximum=2**63 - 1,
        )
        authority_sha = _digest(self.authority_sha256, "authority_sha256")
        timeout = _positive_float(
            self.timeout_seconds,
            "timeout_seconds",
            minimum=0.1,
            maximum=60.0,
        )
        max_response = _positive_int(
            self.max_response_bytes,
            "max_response_bytes",
            maximum=_MAX_RESPONSE_BYTES,
        )
        attempts = _positive_int(
            self.retry_attempts,
            "retry_attempts",
            maximum=5,
        )
        backoff = _positive_float(
            self.retry_backoff_seconds,
            "retry_backoff_seconds",
            minimum=0.01,
            maximum=30.0,
        )
        idempotency_name = self.idempotency_header_name
        if idempotency_name is not None:
            if not isinstance(idempotency_name, str) or not re.fullmatch(
                r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}", idempotency_name
            ):
                raise ValueError("provider idempotency header is invalid")
            matches = [
                value
                for name, value in headers.items()
                if name.lower() == idempotency_name.lower()
            ]
            if len(matches) != 1:
                raise ValueError("provider idempotency header is missing")
        material = {
            "schema": self.schema,
            "tenant_sha256": tenant,
            "provider": provider,
            "operation": operation,
            "method": method,
            "url_sha256": _sha256(self.url),
            "header_names": sorted(name.lower() for name in headers),
            "headers_sha256": _sha256(headers),
            "json_body_sha256": _sha256(body),
            "request_sha256": request_sha,
            "authority_revision": authority_revision,
            "authority_sha256": authority_sha,
            "timeout_seconds": timeout,
            "max_response_bytes": max_response,
            "retry_attempts": attempts,
            "retry_backoff_seconds": backoff,
            "idempotency_header_name": idempotency_name,
        }
        if _digest(self.operation_sha256, "operation_sha256") != _sha256(material):
            raise ValueError("provider operation digest does not match")
        object.__setattr__(self, "tenant_sha256", tenant)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "json_body", body)
        object.__setattr__(self, "request_sha256", request_sha)
        object.__setattr__(self, "authority_revision", authority_revision)
        object.__setattr__(self, "authority_sha256", authority_sha)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "max_response_bytes", max_response)
        object.__setattr__(self, "retry_attempts", attempts)
        object.__setattr__(self, "retry_backoff_seconds", backoff)
        object.__setattr__(self, "operation_sha256", _sha256(material))

    @classmethod
    def build(cls, **kwargs: Any) -> FixedProviderOperation:
        """Build the operation digest without retaining or displaying secrets."""

        provisional = dict(kwargs)
        provisional["schema"] = PROVIDER_OPERATION_SCHEMA
        provisional["operation_sha256"] = "0" * 64
        candidate = object.__new__(cls)
        for field, value in provisional.items():
            object.__setattr__(candidate, field, value)
        # Derive the exact same closed material used by __post_init__ without
        # relying on a partly validated object.
        tenant = _digest(provisional["tenant_sha256"], "tenant_sha256")
        provider = _provider(provisional["provider"])
        operation = str(provisional["operation"]).strip().lower()
        method = str(provisional["method"]).strip().upper()
        headers = _headers(provisional["headers"])
        body = _body(provisional.get("json_body"))
        timeout = float(provisional["timeout_seconds"])
        material = {
            "schema": PROVIDER_OPERATION_SCHEMA,
            "tenant_sha256": tenant,
            "provider": provider,
            "operation": operation,
            "method": method,
            "url_sha256": _sha256(provisional["url"]),
            "header_names": sorted(name.lower() for name in headers),
            "headers_sha256": _sha256(headers),
            "json_body_sha256": _sha256(body),
            "request_sha256": _digest(
                provisional["request_sha256"], "request_sha256"
            ),
            "authority_revision": provisional["authority_revision"],
            "authority_sha256": _digest(
                provisional["authority_sha256"], "authority_sha256"
            ),
            "timeout_seconds": timeout,
            "max_response_bytes": provisional["max_response_bytes"],
            "retry_attempts": provisional["retry_attempts"],
            "retry_backoff_seconds": float(
                provisional["retry_backoff_seconds"]
            ),
            "idempotency_header_name": provisional.get(
                "idempotency_header_name"
            ),
        }
        provisional["operation_sha256"] = _sha256(material)
        return cls(**provisional)


class ProviderHTTPTransport(Protocol):
    dns_pinned: bool
    redirects_followed: bool

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None,
        timeout: float,
        max_response_bytes: int,
    ) -> BoundedHTTPResponse: ...


class ObservationWireAuthority(Protocol):
    tenant_sha256: str
    provider: str
    authority_revision: int
    authority_sha256: str

    def build_observation_operation(
        self,
        purpose: ObservationPurpose,
        request: FederationObservationRequest | ConditionalAccessObservationRequest,
    ) -> FixedProviderOperation: ...

    def parse_federation_observation(
        self,
        request: FederationObservationRequest,
        operation: FixedProviderOperation,
        response: BoundedHTTPResponse,
    ) -> AgentPrincipalFederationObservation: ...

    def parse_conditional_access_observation(
        self,
        request: ConditionalAccessObservationRequest,
        operation: FixedProviderOperation,
        response: BoundedHTTPResponse,
    ) -> ConditionalAccessObservation: ...


class PurviewWireAuthority(Protocol):
    tenant_sha256: str
    provider: str
    authority_revision: int
    authority_sha256: str

    def build_purview_operation(
        self, request: PurviewWritebackRequest
    ) -> FixedProviderOperation: ...

    def parse_purview_ack(
        self,
        request: PurviewWritebackRequest,
        operation: FixedProviderOperation,
        response: BoundedHTTPResponse,
    ) -> PurviewWritebackAck: ...

    def recover_purview_ack(
        self,
        request: PurviewWritebackRequest,
        operation: FixedProviderOperation,
    ) -> PurviewWritebackAck | None: ...


class _FixedAuthorityTransport:
    dns_pinned = True
    redirects_followed = False

    def __init__(
        self,
        *,
        tenant_sha256: str,
        provider: Provider,
        fixed_hosts: Sequence[str],
        authority: object,
        http: ProviderHTTPTransport,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tenant_sha256 = _digest(tenant_sha256, "tenant_sha256")
        self.provider = _provider(provider)
        if isinstance(fixed_hosts, (str, bytes)) or not fixed_hosts:
            raise ValueError("provider fixed hosts are required")
        hosts = frozenset(_host(value) for value in fixed_hosts)
        if len(hosts) != len(fixed_hosts) or len(hosts) > 8:
            raise ValueError("provider fixed hosts are invalid")
        if (
            getattr(authority, "tenant_sha256", None) != self.tenant_sha256
            or getattr(authority, "provider", None) != self.provider
        ):
            raise IdentityGovernanceControlConfigError(
                "provider_wire_authority_binding_invalid"
            )
        self._authority_revision = _positive_int(
            getattr(authority, "authority_revision", None),
            "authority_revision",
            maximum=2**63 - 1,
        )
        self._authority_sha256 = _digest(
            getattr(authority, "authority_sha256", None),
            "authority_sha256",
        )
        if (
            http is None
            or getattr(http, "dns_pinned", None) is not True
            or getattr(http, "redirects_followed", None) is not False
            or not callable(getattr(http, "request", None))
        ):
            raise IdentityGovernanceControlConfigError(
                "provider_wire_http_boundary_invalid"
            )
        if not callable(sleeper):
            raise TypeError("provider wire sleeper must be callable")
        self._fixed_hosts = hosts
        self._authority = authority
        self._http = http
        self._sleeper = sleeper

    def _validate_operation(
        self,
        operation: object,
        *,
        request_sha256: str,
        expected_operation: ProviderOperation,
    ) -> FixedProviderOperation:
        if not isinstance(operation, FixedProviderOperation):
            raise IdentityGovernanceControlConfigError(
                "provider_wire_operation_invalid"
            )
        if (
            operation.tenant_sha256 != self.tenant_sha256
            or operation.provider != self.provider
            or operation.operation != expected_operation
            or operation.request_sha256 != request_sha256
            or operation.authority_revision != self._authority_revision
            or operation.authority_sha256 != self._authority_sha256
        ):
            raise IdentityGovernanceBindingError(
                "provider_wire_operation_binding_mismatch"
            )
        _fixed_url(operation.url, self._fixed_hosts)
        return operation

    def _send(self, operation: FixedProviderOperation) -> BoundedHTTPResponse:
        response = self._http.request(
            operation.method,
            operation.url,
            headers=operation.headers,
            json_body=operation.json_body,
            timeout=operation.timeout_seconds,
            max_response_bytes=operation.max_response_bytes,
        )
        if not isinstance(response, BoundedHTTPResponse):
            raise IdentityGovernanceDeliveryError(
                "provider_wire_response_invalid"
            )
        if response.final_url != operation.url:
            raise IdentityGovernanceDeliveryError(
                "provider_wire_redirect_rejected"
            )
        if len(response.body) > operation.max_response_bytes:
            raise IdentityGovernanceDeliveryError(
                "provider_wire_response_too_large"
            )
        return response

    def _wait(self, operation: FixedProviderOperation, attempt: int) -> None:
        self._sleeper(
            min(operation.retry_backoff_seconds * (2**attempt), 30.0)
        )

    def status(self) -> dict[str, Any]:
        return {
            "schema": "lightwork.identity_governance.fixed-provider-transport-status.v1",
            "tenant_sha256": self.tenant_sha256,
            "provider": self.provider,
            "fixed_hosts": sorted(self._fixed_hosts),
            "dns_pinned": True,
            "redirects_followed": False,
            "bounded_requests": True,
            "bounded_responses": True,
            "independent_authority_bound": True,
            "authority_revision": self._authority_revision,
            "authority_sha256": self._authority_sha256,
            "credentials_exposed": False,
            "provider_contract_supplied_by_deployment": True,
            "provider_contract_accepted": False,
            "live_acceptance_verified": False,
        }


class FixedAuthorityObservationTransport(_FixedAuthorityTransport):
    """Bounded Entra/Okta federation or conditional-access reader."""

    def __init__(self, *, purpose: ObservationPurpose, **kwargs: Any) -> None:
        if purpose not in {"federation", "conditional_access"}:
            raise ValueError("provider observation purpose is invalid")
        super().__init__(**kwargs)
        if self.provider not in {"entra", "okta"}:
            raise IdentityGovernanceControlConfigError(
                "provider_observation_authority_invalid"
            )
        required = (
            "build_observation_operation",
            "parse_federation_observation",
            "parse_conditional_access_observation",
        )
        if any(not callable(getattr(self._authority, name, None)) for name in required):
            raise IdentityGovernanceControlConfigError(
                "provider_observation_authority_invalid"
            )
        self._purpose: ObservationPurpose = purpose

    def observe(
        self,
        request: FederationObservationRequest | ConditionalAccessObservationRequest,
    ) -> AgentPrincipalFederationObservation | ConditionalAccessObservation:
        expected_type = (
            FederationObservationRequest
            if self._purpose == "federation"
            else ConditionalAccessObservationRequest
        )
        if not isinstance(request, expected_type):
            raise IdentityGovernanceBindingError(
                "provider_observation_request_invalid"
            )
        _validate_observation_request(request, purpose=self._purpose)
        if (
            request.tenant_sha256 != self.tenant_sha256
            or request.provider != self.provider
        ):
            raise IdentityGovernanceBindingError(
                "provider_observation_request_binding_mismatch"
            )
        request_sha = _request_sha256(request)
        try:
            candidate = self._authority.build_observation_operation(
                self._purpose,
                request,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError(
                "provider_observation_authority_unavailable"
            ) from None
        expected_operation: ProviderOperation = (
            "federation_observe"
            if self._purpose == "federation"
            else "conditional_access_observe"
        )
        operation = self._validate_operation(
            candidate,
            request_sha256=request_sha,
            expected_operation=expected_operation,
        )
        response: BoundedHTTPResponse | None = None
        for attempt in range(operation.retry_attempts):
            try:
                response = self._send(operation)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                if attempt + 1 >= operation.retry_attempts:
                    raise IdentityGovernanceDeliveryError(
                        "provider_observation_unavailable"
                    ) from None
                self._wait(operation, attempt)
                continue
            if 200 <= response.status_code < 300:
                break
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < operation.retry_attempts:
                    self._wait(operation, attempt)
                    continue
            raise IdentityGovernanceDeliveryError(
                "provider_observation_rejected"
            )
        if response is None or not 200 <= response.status_code < 300:
            raise IdentityGovernanceDeliveryError(
                "provider_observation_unavailable"
            )
        try:
            if self._purpose == "federation":
                result = self._authority.parse_federation_observation(
                    request,
                    operation,
                    response,
                )
            else:
                result = self._authority.parse_conditional_access_observation(
                    request,
                    operation,
                    response,
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError(
                "provider_observation_response_invalid"
            ) from None
        if self._purpose == "federation":
            if not isinstance(result, AgentPrincipalFederationObservation):
                raise IdentityGovernanceDeliveryError(
                    "provider_observation_response_invalid"
                )
        elif not isinstance(result, ConditionalAccessObservation):
            raise IdentityGovernanceDeliveryError(
                "provider_observation_response_invalid"
            )
        expected = (
            request.tenant_sha256,
            request.provider,
            request.principal_ref,
            request.provider_subject_id,
            request.binding_sha256,
            request.binding_revision,
        )
        actual = (
            result.tenant_sha256,
            result.provider,
            result.principal_ref,
            result.provider_subject_id,
            result.binding_sha256,
            result.binding_revision,
        )
        if actual != expected or result.live_acceptance_verified is not False:
            raise IdentityGovernanceBindingError(
                "provider_observation_response_binding_mismatch"
            )
        return result


class FixedAuthorityPurviewWritebackTransport(_FixedAuthorityTransport):
    """Purview write boundary with exact idempotency and ambiguity recovery."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self.provider != "purview" or any(
            not callable(getattr(self._authority, name, None))
            for name in (
                "build_purview_operation",
                "parse_purview_ack",
                "recover_purview_ack",
            )
        ):
            raise IdentityGovernanceControlConfigError(
                "purview_wire_authority_invalid"
            )

    @staticmethod
    def _validate_ack(
        request: PurviewWritebackRequest,
        value: object,
    ) -> PurviewWritebackAck:
        if not isinstance(value, PurviewWritebackAck):
            raise IdentityGovernanceDeliveryError("purview_wire_ack_invalid")
        expected = (
            request.operation,
            request.tenant_sha256,
            request.intent_id,
            request.request_sha256,
            request.idempotency_sha256,
        )
        actual = (
            value.operation,
            value.tenant_sha256,
            value.intent_id,
            value.request_sha256,
            value.idempotency_sha256,
        )
        if (
            actual != expected
            or value.accepted is not True
            or value.live_acceptance_verified is not False
        ):
            raise IdentityGovernanceBindingError("purview_wire_ack_binding_mismatch")
        return value

    def _recover(
        self,
        request: PurviewWritebackRequest,
        operation: FixedProviderOperation,
    ) -> PurviewWritebackAck | None:
        try:
            recovered = self._authority.recover_purview_ack(request, operation)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return None
        return (
            None
            if recovered is None
            else self._validate_ack(request, recovered)
        )

    def _execute(self, request: PurviewWritebackRequest) -> PurviewWritebackAck:
        if not isinstance(request, PurviewWritebackRequest):
            raise IdentityGovernanceBindingError("purview_wire_request_invalid")
        if request.tenant_sha256 != self.tenant_sha256:
            raise IdentityGovernanceBindingError(
                "purview_wire_request_binding_mismatch"
            )
        request_sha = _request_sha256(request)
        try:
            candidate = self._authority.build_purview_operation(request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise IdentityGovernanceDeliveryError(
                "purview_wire_authority_unavailable"
            ) from None
        expected_operation: ProviderOperation = (
            "purview_apply"
            if request.operation == "apply"
            else "purview_compensate"
        )
        operation = self._validate_operation(
            candidate,
            request_sha256=request_sha,
            expected_operation=expected_operation,
        )
        header_name = operation.idempotency_header_name
        if header_name is None:
            raise IdentityGovernanceControlConfigError(
                "purview_wire_idempotency_proof_missing"
            )
        idempotency_values = [
            value
            for name, value in operation.headers.items()
            if name.lower() == header_name.lower()
        ]
        if idempotency_values != [request.idempotency_sha256]:
            raise IdentityGovernanceBindingError(
                "purview_wire_idempotency_binding_mismatch"
            )
        for attempt in range(operation.retry_attempts):
            response: BoundedHTTPResponse | None = None
            ambiguous = False
            try:
                response = self._send(operation)
                ambiguous = (
                    response.status_code == 429 or response.status_code >= 500
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                ambiguous = True
            if response is not None and 200 <= response.status_code < 300:
                try:
                    parsed = self._authority.parse_purview_ack(
                        request,
                        operation,
                        response,
                    )
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception:
                    raise IdentityGovernanceDeliveryError(
                        "purview_wire_ack_invalid"
                    ) from None
                return self._validate_ack(request, parsed)
            if ambiguous:
                recovered = self._recover(request, operation)
                if recovered is not None:
                    return recovered
                if attempt + 1 < operation.retry_attempts:
                    self._wait(operation, attempt)
                    continue
                raise IdentityGovernanceDeliveryError(
                    "purview_wire_ambiguous_unrecovered"
                )
            raise IdentityGovernanceDeliveryError("purview_wire_rejected")
        raise IdentityGovernanceDeliveryError("purview_wire_unavailable")

    def apply(self, request: PurviewWritebackRequest) -> PurviewWritebackAck:
        if not isinstance(request, PurviewWritebackRequest) or request.operation != "apply":
            raise IdentityGovernanceBindingError("purview_wire_operation_mismatch")
        return self._execute(request)

    def compensate(self, request: PurviewWritebackRequest) -> PurviewWritebackAck:
        if (
            not isinstance(request, PurviewWritebackRequest)
            or request.operation != "compensate"
        ):
            raise IdentityGovernanceBindingError("purview_wire_operation_mismatch")
        return self._execute(request)

    def status(self) -> dict[str, Any]:
        return {
            **super().status(),
            "schema": "lightwork.identity_governance.fixed-purview-transport-status.v1",
            "write_idempotency_required": True,
            "ambiguity_recovery_required": True,
            "provider_writes_possible": True,
            "provider_write_accepted": False,
            "live_acceptance_verified": False,
        }


__all__ = [
    "FixedAuthorityObservationTransport",
    "FixedAuthorityPurviewWritebackTransport",
    "FixedProviderOperation",
    "ObservationWireAuthority",
    "PROVIDER_OPERATION_SCHEMA",
    "ProviderHTTPTransport",
    "PurviewWireAuthority",
]
