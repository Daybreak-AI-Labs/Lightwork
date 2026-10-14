"""Evidence-backed certification contracts for the small env-hunt connector core.

Static declarations and deterministic fixture tests establish only the
``contract-tested`` tier.  ``live-certified`` is reserved for a fresh probe
receipt whose Ed25519 signature verifies against an operator-supplied trust
anchor.  The probe boundary accepts metadata only: credentials, response
bodies, raw headers, and raw telemetry are never included in a receipt.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from functools import partial
from types import CodeType, MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .. import __version__ as LIGHTWORK_VERSION
from .connectors import (
    parse_cloudtrail,
    parse_entra,
    parse_github,
    parse_guardduty,
    parse_okta,
    parse_siem,
)
from .models import QueryRequest, TelemetryEvent

MANIFEST_VERSION = 1
LEGACY_RECEIPT_VERSION = 1
RECEIPT_VERSION = 2
CONTRACT_ALGORITHM_VERSION = "env-hunt-connector-contract-v1"
IMPLEMENTATION_DIGEST_ALGORITHM = "python-callable-semantic-v1"

DECLARED = "declared"
CONTRACT_TESTED = "contract-tested"
LIVE_CERTIFIED = "live-certified"

_MAX_RECEIPT_BYTES = 128 * 1024
_MAX_RECEIPT_TTL_SECONDS = 7 * 24 * 3600
_CLOCK_SKEW_SECONDS = 300
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/@+-]{0,159}\Z")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}\Z")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_HEX_32_RE = re.compile(r"[0-9a-fA-F]{64}\Z")
_HEX_SIG_RE = re.compile(r"[0-9a-fA-F]{128}\Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _constant_fingerprint(value: object) -> object:
    """Describe code constants without ever returning their literal values."""
    if isinstance(value, CodeType):
        return {"code": _code_fingerprint(value)}
    if isinstance(value, tuple):
        return {"tuple": [_constant_fingerprint(item) for item in value]}
    raw = repr(value).encode("utf-8", errors="backslashreplace")
    return {
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _code_fingerprint(code: CodeType) -> dict[str, object]:
    """Return a path- and line-number-independent semantic code descriptor."""
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "stacksize": code.co_stacksize,
        "flags": code.co_flags,
        "bytecode_sha256": hashlib.sha256(code.co_code).hexdigest(),
        "constants": [_constant_fingerprint(value) for value in code.co_consts],
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }


def callable_implementation_digest(transport: Callable[..., object]) -> str:
    """Return a deterministic implementation digest without serializing state.

    Defaults, partial arguments, closure cells, and callable-instance fields are
    intentionally excluded because those locations commonly contain credentials.
    The exact non-secret runtime descriptor is bound separately by
    :class:`TransportBinding`.
    """
    if not callable(transport):
        raise TypeError("connector transport must be callable")
    candidate: object = transport
    partial_depth = 0
    while isinstance(candidate, partial):
        partial_depth += 1
        candidate = candidate.func
    owner_type = ""
    if inspect.ismethod(candidate):
        function = candidate.__func__
        owner = candidate.__self__
        if owner is not None:
            owner_cls = owner if inspect.isclass(owner) else type(owner)
            owner_type = f"{owner_cls.__module__}.{owner_cls.__qualname__}"
    elif inspect.isfunction(candidate):
        function = candidate
    else:
        owner_cls = type(candidate)
        owner_type = f"{owner_cls.__module__}.{owner_cls.__qualname__}"
        function = next(
            (
                base.__dict__["__call__"]
                for base in owner_cls.__mro__
                if "__call__" in base.__dict__
            ),
            None,
        )
    code = getattr(function, "__code__", None)
    if not isinstance(code, CodeType):
        raise TypeError(
            "certified connector transport must have inspectable Python implementation"
        )
    return _digest({
        "algorithm": IMPLEMENTATION_DIGEST_ALGORITHM,
        "module": str(getattr(function, "__module__", "")),
        "qualname": str(getattr(function, "__qualname__", "")),
        "owner_type": owner_type,
        "partial_depth": partial_depth,
        "code": _code_fingerprint(code),
    })


@dataclass(frozen=True)
class AuthContract:
    modes: tuple[str, ...]
    minimum_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.modes or any(not _IDENTIFIER_RE.fullmatch(item) for item in self.modes):
            raise ValueError("connector auth modes must be bounded identifiers")
        if not self.minimum_scopes or any(
            not _IDENTIFIER_RE.fullmatch(item) for item in self.minimum_scopes
        ):
            raise ValueError("connector minimum scopes must be bounded identifiers")


@dataclass(frozen=True)
class TransportContract:
    allowed_hosts: tuple[str, ...]
    allowed_ports: tuple[int, ...]
    api_version: str
    effect_classification: str
    read_only: bool
    pagination: str
    cursor: str
    rate_limit: str
    deadline_seconds: float
    max_response_bytes: int
    max_pages: int
    max_records: int

    def __post_init__(self) -> None:
        if not self.allowed_hosts or any(not item or len(item) > 253 for item in self.allowed_hosts):
            raise ValueError("connector allowed hosts are invalid")
        if not self.allowed_ports or any(
            isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
            for port in self.allowed_ports
        ):
            raise ValueError("connector allowed ports are invalid")
        if not _TOKEN_RE.fullmatch(self.api_version):
            raise ValueError("connector API version is invalid")
        if self.effect_classification != "read" or self.read_only is not True:
            raise ValueError("certified env-hunt connectors must be read-only")
        for value in (self.pagination, self.cursor, self.rate_limit):
            if not value or len(value) > 160:
                raise ValueError("connector transport contract metadata is invalid")
        if not 0 < self.deadline_seconds <= 120:
            raise ValueError("connector deadline must be positive and bounded")
        if not 1 <= self.max_response_bytes <= 64 * 1024 * 1024:
            raise ValueError("connector response-byte limit is invalid")
        if not 1 <= self.max_pages <= 1000 or not 1 <= self.max_records <= 100_000:
            raise ValueError("connector pagination bounds are invalid")


@dataclass(frozen=True)
class ContractTestSpec:
    name: str
    vector_sha256: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER_RE.fullmatch(self.name) or not _HEX_32_RE.fullmatch(
            self.vector_sha256
        ):
            raise ValueError("connector contract-test specification is invalid")


@dataclass(frozen=True)
class ConnectorCertificationManifest:
    connector: str
    display_name: str
    manifest_version: int
    lightwork_version: str
    auth: AuthContract
    transport: TransportContract
    contract_tests: tuple[ContractTestSpec, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.connector):
            raise ValueError("connector manifest name is invalid")
        if not self.display_name or len(self.display_name) > 120:
            raise ValueError("connector display name is invalid")
        if self.manifest_version != MANIFEST_VERSION:
            raise ValueError("unsupported connector manifest version")
        if self.lightwork_version != LIGHTWORK_VERSION:
            raise ValueError("connector manifest Lightwork version is stale")
        if len(self.contract_tests) < 5:
            raise ValueError("connector manifest lacks the required contract tests")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @property
    def test_hashes(self) -> tuple[str, ...]:
        return tuple(test.vector_sha256 for test in self.contract_tests)


@dataclass(frozen=True)
class TransportBinding:
    """Immutable identity of the exact runtime transport a receipt authorizes."""

    connector: str
    endpoint_host: str
    endpoint_port: int
    api_version: str
    auth_mode: str
    granted_scopes: tuple[str, ...]
    tenant: str
    implementation_sha256: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.connector):
            raise ValueError("transport binding connector is invalid")
        try:
            canonical_host = self.endpoint_host.encode("idna").decode("ascii").lower()
        except (AttributeError, UnicodeError):
            canonical_host = ""
        if (
            not canonical_host
            or canonical_host != self.endpoint_host
            or len(canonical_host) > 253
            or ":" in canonical_host
        ):
            raise ValueError("transport binding endpoint host must be canonical")
        if (
            isinstance(self.endpoint_port, bool)
            or not isinstance(self.endpoint_port, int)
            or not 1 <= self.endpoint_port <= 65535
        ):
            raise ValueError("transport binding endpoint port is invalid")
        if not _TOKEN_RE.fullmatch(self.api_version):
            raise ValueError("transport binding API version is invalid")
        if not _IDENTIFIER_RE.fullmatch(self.auth_mode):
            raise ValueError("transport binding auth mode is invalid")
        if (
            not isinstance(self.granted_scopes, tuple)
            or not self.granted_scopes
            or any(not _IDENTIFIER_RE.fullmatch(scope) for scope in self.granted_scopes)
            or self.granted_scopes != tuple(sorted(set(self.granted_scopes)))
        ):
            raise ValueError("transport binding scopes must be exact, unique, and canonical")
        if (
            not isinstance(self.tenant, str)
            or len(self.tenant) > 160
            or (self.tenant and not _TOKEN_RE.fullmatch(self.tenant))
        ):
            raise ValueError("transport binding tenant is invalid")
        if (
            not isinstance(self.implementation_sha256, str)
            or not _HEX_32_RE.fullmatch(self.implementation_sha256)
            or self.implementation_sha256 != self.implementation_sha256.lower()
        ):
            raise ValueError("transport binding implementation digest is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "endpoint_host": self.endpoint_host,
            "endpoint_port": self.endpoint_port,
            "api_version": self.api_version,
            "auth_mode": self.auth_mode,
            "granted_scopes": list(self.granted_scopes),
            "tenant": self.tenant,
            "implementation_sha256": self.implementation_sha256,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: TransportBinding | Mapping[str, Any]) -> TransportBinding:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("transport binding must be an object")
        expected = {
            "connector",
            "endpoint_host",
            "endpoint_port",
            "api_version",
            "auth_mode",
            "granted_scopes",
            "tenant",
            "implementation_sha256",
        }
        if set(value) != expected:
            raise ValueError("transport binding schema is invalid")
        scopes = value.get("granted_scopes")
        if not isinstance(scopes, (list, tuple)):
            raise ValueError("transport binding scopes must be a sequence")
        return cls(
            connector=str(value.get("connector", "")),
            endpoint_host=str(value.get("endpoint_host", "")),
            endpoint_port=value.get("endpoint_port"),
            api_version=str(value.get("api_version", "")),
            auth_mode=str(value.get("auth_mode", "")),
            granted_scopes=tuple(str(scope) for scope in scopes),
            tenant=str(value.get("tenant", "")),
            implementation_sha256=str(value.get("implementation_sha256", "")),
        )


@dataclass(frozen=True)
class ContractCheck:
    name: str
    vector_sha256: str
    passed: bool
    detail_code: str


@dataclass(frozen=True)
class ContractTestResult:
    connector: str
    manifest_sha256: str
    checks: tuple[ContractCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    @property
    def status(self) -> str:
        return CONTRACT_TESTED if self.passed else DECLARED


@dataclass(frozen=True)
class LiveProbeObservation:
    """Sanitized metadata returned by an injected, credential-owning probe."""

    endpoint_url: str
    api_version: str
    auth_mode: str
    granted_scopes: tuple[str, ...]
    status_code: int
    response_bytes: int
    elapsed_ms: float
    pages: int
    cursor_observed: bool
    rate_limit: dict[str, int | float | bool | None]
    read_only: bool = True
    passed: bool = True
    detail_code: str = "probe_passed"


@runtime_checkable
class LiveProbe(Protocol):
    def __call__(
        self, manifest: ConnectorCertificationManifest
    ) -> LiveProbeObservation | Mapping[str, Any]: ...


ReceiptSigner = Callable[[bytes], Mapping[str, str]]
AuditRecorder = Callable[[str, dict[str, Any]], bool]


@dataclass(frozen=True)
class ReceiptVerification:
    valid: bool
    status: str
    reason: str
    signer_key_id: str = ""


@dataclass(frozen=True)
class CertificationAssessment:
    connector: str
    status: str
    manifest_sha256: str
    contract_passed: bool
    live_receipt_valid: bool
    reason: str


@dataclass(frozen=True)
class ConnectorUseDecision:
    connector: str
    allowed: bool
    certification_status: str
    reason: str
    override_audited: bool = False


@dataclass(frozen=True)
class CertificationPolicy:
    certified_only: bool
    allow_uncertified: bool
    enterprise: bool
    config_degraded: bool = False


_CONTRACT_VECTORS: dict[str, dict[str, Any]] = {
    "cloudtrail": {
        "display_name": "AWS CloudTrail",
        "parser": parse_cloudtrail,
        "row": {
            "eventID": "ct-1",
            "eventTime": 1_704_067_200,
            "eventName": "ConsoleLogin",
            "userIdentity": {"arn": "arn:aws:iam::123456789012:user/alice"},
        },
        "expected": ("aws.cloudtrail", "ConsoleLogin"),
        "endpoint": "https://cloudtrail.us-east-1.amazonaws.com",
        "auth": (("aws_sigv4_assume_role", "aws_workload_identity"), ("cloudtrail:LookupEvents",)),
        "hosts": ("cloudtrail.amazonaws.com", "cloudtrail.{region}.amazonaws.com"),
        "ports": (443,),
        "api_version": "2013-11-01",
        "pagination": "NextToken with max_pages and max_records",
        "cursor": "NextToken is opaque and never logged",
    },
    "guardduty": {
        "display_name": "AWS GuardDuty",
        "parser": parse_guardduty,
        "row": {
            "id": "gd-1",
            "updatedAt": 1_704_067_200,
            "type": "UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B",
            "severity": 8,
        },
        "expected": ("aws.guardduty", "UnauthorizedAccess:IAMUser/ConsoleLoginSuccess.B"),
        "endpoint": "https://guardduty.us-east-1.amazonaws.com",
        "auth": (
            ("aws_sigv4_assume_role", "aws_workload_identity"),
            ("guardduty:ListFindings", "guardduty:GetFindings"),
        ),
        "hosts": ("guardduty.{region}.amazonaws.com",),
        "ports": (443,),
        "api_version": "2017-11-28",
        "pagination": "NextToken with max_pages and max_records",
        "cursor": "NextToken is opaque and never logged",
    },
    "entra": {
        "display_name": "Microsoft Entra",
        "parser": parse_entra,
        "row": {
            "id": "entra-1",
            "createdDateTime": "2024-01-01T00:00:00Z",
            "activityDisplayName": "User logged in",
            "result": "success",
        },
        "expected": ("identity.entra", "User logged in"),
        "endpoint": "https://graph.microsoft.com",
        "auth": (("oauth2_client_credentials", "managed_identity"), ("AuditLog.Read.All",)),
        "hosts": ("graph.microsoft.com",),
        "ports": (443,),
        "api_version": "v1.0",
        "pagination": "@odata.nextLink with max_pages and max_records",
        "cursor": "@odata.nextLink host is revalidated before every page",
    },
    "sentinel": {
        "display_name": "Microsoft Sentinel",
        "parser": parse_siem,
        "row": {
            "id": "sentinel-1",
            "siem": "sentinel",
            "TimeGenerated": "2024-01-01T00:00:00Z",
            "OperationName": "SigninLogs",
        },
        "expected": ("siem.sentinel", "SigninLogs"),
        "endpoint": "https://api.loganalytics.io",
        "auth": (("oauth2_client_credentials", "managed_identity"), ("Data.Read",)),
        "hosts": ("api.loganalytics.io",),
        "ports": (443,),
        "api_version": "v1",
        "pagination": "response continuation token with max_pages and max_records",
        "cursor": "continuation token is opaque and never logged",
    },
    "okta": {
        "display_name": "Okta System Log",
        "parser": parse_okta,
        "row": {
            "uuid": "okta-1",
            "published": "2024-01-01T00:00:00Z",
            "eventType": "user.session.start",
            "outcome": {"result": "SUCCESS"},
        },
        "expected": ("identity.okta", "user.session.start"),
        "endpoint": "https://tenant.okta.com",
        "auth": (("oauth2_private_key_jwt", "scoped_api_token"), ("okta.logs.read",)),
        "hosts": (
            "{tenant}.okta.com",
            "{tenant}.okta-emea.com",
            "{tenant}.oktapreview.com",
        ),
        "ports": (443,),
        "api_version": "v1",
        "pagination": "Link rel=next with max_pages and max_records",
        "cursor": "next link host is revalidated before every page",
    },
    "splunk": {
        "display_name": "Splunk Enterprise or Cloud",
        "parser": parse_siem,
        "row": {
            "id": "splunk-1",
            "siem": "splunk",
            "_time": 1_704_067_200,
            "action": "search_result",
        },
        "expected": ("siem.splunk", "search_result"),
        "endpoint": "https://splunk.customer.example:8089",
        "auth": (("splunk_bearer_token", "mtls"), ("search",)),
        "hosts": ("$operator_pinned",),
        "ports": (443, 8089),
        "api_version": "v2",
        "pagination": "search job offset/count with max_pages and max_records",
        "cursor": "search job id is opaque and never logged",
    },
    "github": {
        "display_name": "GitHub Audit and Security",
        "parser": parse_github,
        "row": {
            "_document_id": "github-1",
            "@timestamp": 1_704_067_200_000,
            "action": "repo.create",
            "actor": "octocat",
            "repo": "example/repository",
        },
        "expected": ("github.audit", "repo.create"),
        "endpoint": "https://api.github.com",
        "auth": (("github_app_installation", "fine_grained_token"), ("read:audit_log", "security_events")),
        "hosts": ("api.github.com", "{enterprise}.ghe.com", "$operator_pinned"),
        "ports": (443,),
        "api_version": "2022-11-28",
        "pagination": "Link rel=next with max_pages and max_records",
        "cursor": "next link host is revalidated before every page",
    },
    "elastic": {
        "display_name": "Elastic Security",
        "parser": parse_siem,
        "row": {
            "id": "elastic-1",
            "vendor": "elastic",
            "@timestamp": "2024-01-01T00:00:00Z",
            "event": {"action": "authentication_success", "category": "authentication"},
        },
        "expected": ("siem.elastic", "authentication_success"),
        "endpoint": "https://elastic.customer.example:9243",
        "auth": (("elastic_api_key", "oauth2_bearer"), ("read_security_indices",)),
        "hosts": ("$operator_pinned",),
        "ports": (443, 9200, 9243),
        "api_version": "v8",
        "pagination": "PIT plus search_after with max_pages and max_records",
        "cursor": "PIT and search_after are opaque and never logged",
    },
}


def _test_specs(name: str, vector: Mapping[str, Any]) -> tuple[ContractTestSpec, ...]:
    values: tuple[tuple[str, object], ...] = (
        ("parser", {"row": vector["row"], "expected": vector["expected"]}),
        ("endpoint", {"endpoint": vector["endpoint"], "hosts": vector["hosts"]}),
        ("read_only", {"effect": "read", "read_only": True}),
        ("bounds", {"deadline": 30, "bytes": 8 * 1024 * 1024, "pages": 100}),
        (
            "rate_limit",
            {"status": 429, "retry-after": "17", "x-ratelimit-remaining": "0"},
        ),
    )
    return tuple(
        ContractTestSpec(
            name=f"{name}.{test_name}.v1",
            vector_sha256=_digest({
                "algorithm": CONTRACT_ALGORITHM_VERSION,
                "connector": name,
                "test": test_name,
                "vector": value,
            }),
        )
        for test_name, value in values
    )


def _build_manifests() -> dict[str, ConnectorCertificationManifest]:
    manifests: dict[str, ConnectorCertificationManifest] = {}
    for name, vector in _CONTRACT_VECTORS.items():
        modes, scopes = vector["auth"]
        manifests[name] = ConnectorCertificationManifest(
            connector=name,
            display_name=str(vector["display_name"]),
            manifest_version=MANIFEST_VERSION,
            lightwork_version=LIGHTWORK_VERSION,
            auth=AuthContract(tuple(modes), tuple(scopes)),
            transport=TransportContract(
                allowed_hosts=tuple(vector["hosts"]),
                allowed_ports=tuple(vector["ports"]),
                api_version=str(vector["api_version"]),
                effect_classification="read",
                read_only=True,
                pagination=str(vector["pagination"]),
                cursor=str(vector["cursor"]),
                rate_limit="HTTP 429 is non-success; retain numeric retry metadata only",
                deadline_seconds=30,
                max_response_bytes=8 * 1024 * 1024,
                max_pages=100,
                max_records=10_000,
            ),
            contract_tests=_test_specs(name, vector),
        )
    return manifests


CERTIFIED_CORE_MANIFESTS: Mapping[str, ConnectorCertificationManifest] = MappingProxyType(
    _build_manifests()
)


def certification_manifest(name: str) -> ConnectorCertificationManifest:
    try:
        return CERTIFIED_CORE_MANIFESTS[str(name).strip().lower()]
    except KeyError as exc:
        raise KeyError(f"connector {name!r} is not in the certified core") from exc


def certification_manifests() -> tuple[ConnectorCertificationManifest, ...]:
    return tuple(CERTIFIED_CORE_MANIFESTS[name] for name in sorted(CERTIFIED_CORE_MANIFESTS))


def _match_host_pattern(host: str, pattern: str) -> bool:
    host_labels = host.split(".")
    pattern_labels = pattern.lower().split(".")
    if len(host_labels) != len(pattern_labels):
        return False
    for actual, declared in zip(host_labels, pattern_labels, strict=True):
        if declared.startswith("{") and declared.endswith("}"):
            if not _DNS_LABEL_RE.fullmatch(actual):
                return False
        elif actual != declared:
            return False
    return True


def _normalize_pinned_host(value: str) -> str | None:
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        parsed = urlsplit(text if "://" in text else f"https://{text}")
    except ValueError:
        return None
    if parsed.username or parsed.password or not parsed.hostname:
        return None
    try:
        return parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def host_allowed(
    manifest_or_name: ConnectorCertificationManifest | str,
    endpoint_url: str,
    *,
    operator_allowed_hosts: tuple[str, ...] = (),
) -> bool:
    """Require HTTPS, an allowed port, and a manifest/operator-pinned exact host."""
    manifest = (
        certification_manifest(manifest_or_name)
        if isinstance(manifest_or_name, str)
        else manifest_or_name
    )
    try:
        parsed = urlsplit(str(endpoint_url))
        port = parsed.port or 443
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    if port not in manifest.transport.allowed_ports:
        return False
    pinned = {
        normalized
        for value in operator_allowed_hosts
        if (normalized := _normalize_pinned_host(value)) is not None
    }
    for pattern in manifest.transport.allowed_hosts:
        if pattern == "$operator_pinned":
            if host in pinned:
                return True
        elif _match_host_pattern(host, pattern):
            return True
    return False


def _canonical_endpoint_descriptor(
    manifest: ConnectorCertificationManifest,
    endpoint_url: str,
    *,
    operator_allowed_hosts: tuple[str, ...],
) -> tuple[str, int]:
    try:
        parsed = urlsplit(str(endpoint_url))
        port = parsed.port or 443
    except (TypeError, ValueError) as exc:
        raise ValueError("connector endpoint is invalid") from exc
    if parsed.query or parsed.path not in ("", "/"):
        raise ValueError("connector endpoint must be a canonical HTTPS origin")
    if not host_allowed(
        manifest,
        endpoint_url,
        operator_allowed_hosts=operator_allowed_hosts,
    ):
        raise ValueError("connector endpoint violates the allowed-host contract")
    if not parsed.hostname:  # pragma: no cover -- guaranteed by host_allowed
        raise ValueError("connector endpoint is invalid")
    return parsed.hostname.encode("idna").decode("ascii").lower(), port


def _exact_scopes(
    manifest: ConnectorCertificationManifest,
    granted_scopes: tuple[str, ...],
) -> tuple[str, ...]:
    if (
        not granted_scopes
        or any(not _IDENTIFIER_RE.fullmatch(scope) for scope in granted_scopes)
        or len(set(granted_scopes)) != len(granted_scopes)
        or set(granted_scopes) != set(manifest.auth.minimum_scopes)
    ):
        raise ValueError("connector must grant exactly the manifest read scopes")
    return tuple(sorted(granted_scopes))


def build_transport_binding(
    name: str,
    transport: Callable[..., object],
    *,
    endpoint_url: str,
    api_version: str,
    auth_mode: str,
    granted_scopes: tuple[str, ...],
    tenant: str,
    operator_allowed_hosts: tuple[str, ...] = (),
) -> TransportBinding:
    """Build the exact non-secret descriptor a live receipt may authorize."""
    manifest = certification_manifest(name)
    host, port = _canonical_endpoint_descriptor(
        manifest,
        endpoint_url,
        operator_allowed_hosts=operator_allowed_hosts,
    )
    if api_version != manifest.transport.api_version:
        raise ValueError("transport binding API version does not match the manifest")
    if auth_mode not in manifest.auth.modes:
        raise ValueError("transport binding auth mode does not match the manifest")
    scopes = _exact_scopes(manifest, tuple(granted_scopes))
    return TransportBinding(
        connector=manifest.connector,
        endpoint_host=host,
        endpoint_port=port,
        api_version=api_version,
        auth_mode=auth_mode,
        granted_scopes=scopes,
        tenant=tenant,
        implementation_sha256=callable_implementation_digest(transport),
    )


def normalize_rate_limit(
    status_code: int,
    headers: Mapping[str, object] | None,
) -> dict[str, int | float | bool | None]:
    """Extract only bounded numeric rate-limit metadata; never retain raw headers."""
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not 100 <= status_code <= 599
    ):
        raise ValueError("HTTP status code must be an integer between 100 and 599")
    lowered = {str(key).strip().lower(): value for key, value in (headers or {}).items()}

    def number(*names: str, maximum: float) -> float | None:
        for name in names:
            if name not in lowered:
                continue
            try:
                value = float(str(lowered[name]).strip())
            except (TypeError, ValueError):
                return None
            if math.isfinite(value) and 0 <= value <= maximum:
                return value
            return None
        return None

    return {
        "status_code": status_code,
        "throttled": status_code == 429,
        "retry_after_seconds": number("retry-after", maximum=86_400),
        "limit": number("x-ratelimit-limit", "x-rate-limit-limit", maximum=1_000_000_000),
        "remaining": number(
            "x-ratelimit-remaining", "x-rate-limit-remaining", maximum=1_000_000_000
        ),
        "reset_at": number("x-ratelimit-reset", "x-rate-limit-reset", maximum=10**11),
    }


def run_contract_tests(name: str) -> ContractTestResult:
    """Run deterministic, network-free tests; success means contract-tested only."""
    manifest = certification_manifest(name)
    vector = _CONTRACT_VECTORS[manifest.connector]
    parser = vector["parser"]
    checks: list[ContractCheck] = []

    try:
        event = parser(vector["row"])
        expected_source, expected_action = vector["expected"]
        parser_ok = isinstance(event, TelemetryEvent) and (
            event.source,
            event.action,
        ) == (expected_source, expected_action)
    except Exception:  # failure-policy: contract_test_failure
        parser_ok = False

    pinned = ()
    if "$operator_pinned" in manifest.transport.allowed_hosts:
        pinned = (urlsplit(str(vector["endpoint"])).hostname or "",)
    endpoint_ok = host_allowed(manifest, str(vector["endpoint"]), operator_allowed_hosts=pinned)
    try:
        QueryRequest(0, 1, read_only=False)
    except ValueError:
        request_rejected_effects = True
    else:  # pragma: no cover -- defensive contract tripwire
        request_rejected_effects = False
    read_ok = (
        manifest.transport.read_only is True
        and manifest.transport.effect_classification == "read"
        and request_rejected_effects
    )
    bounds_ok = (
        0 < manifest.transport.deadline_seconds <= 120
        and 0 < manifest.transport.max_response_bytes <= 64 * 1024 * 1024
        and 0 < manifest.transport.max_pages <= 1000
        and 0 < manifest.transport.max_records <= 100_000
    )
    rate = normalize_rate_limit(
        429,
        {"Retry-After": "17", "X-RateLimit-Remaining": "0", "Authorization": "ignored"},
    )
    rate_ok = (
        rate["throttled"] is True
        and rate["retry_after_seconds"] == 17
        and rate["remaining"] == 0
        and "authorization" not in rate
    )
    outcomes = (parser_ok, endpoint_ok, read_ok, bounds_ok, rate_ok)
    for spec, passed in zip(manifest.contract_tests, outcomes, strict=True):
        checks.append(
            ContractCheck(
                name=spec.name,
                vector_sha256=spec.vector_sha256,
                passed=passed,
                detail_code="passed" if passed else "contract_mismatch",
            )
        )
    return ContractTestResult(manifest.connector, manifest.digest, tuple(checks))


def _observation_from(value: LiveProbeObservation | Mapping[str, Any]) -> LiveProbeObservation:
    if isinstance(value, LiveProbeObservation):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("live probe must return sanitized metadata")
    allowed = {
        "endpoint_url",
        "api_version",
        "auth_mode",
        "granted_scopes",
        "status_code",
        "response_bytes",
        "elapsed_ms",
        "pages",
        "cursor_observed",
        "rate_limit",
        "read_only",
        "passed",
        "detail_code",
    }
    unexpected = sorted(str(key) for key in value if key not in allowed)
    if unexpected:
        raise ValueError(f"live probe returned forbidden metadata fields: {', '.join(unexpected)}")
    scopes = value.get("granted_scopes", ())
    if not isinstance(scopes, (list, tuple)):
        raise ValueError("live probe granted scopes must be a sequence")
    rate_limit = value.get("rate_limit", {})
    if not isinstance(rate_limit, Mapping):
        raise ValueError("live probe rate-limit metadata must be an object")
    return LiveProbeObservation(
        endpoint_url=str(value.get("endpoint_url", "")),
        api_version=str(value.get("api_version", "")),
        auth_mode=str(value.get("auth_mode", "")),
        granted_scopes=tuple(str(scope) for scope in scopes),
        status_code=value.get("status_code", 0),
        response_bytes=value.get("response_bytes", -1),
        elapsed_ms=value.get("elapsed_ms", -1),
        pages=value.get("pages", 0),
        cursor_observed=value.get("cursor_observed", False),
        rate_limit=dict(rate_limit),
        read_only=value.get("read_only", False),
        passed=value.get("passed", False),
        detail_code=str(value.get("detail_code", "")),
    )


def _validated_observation(
    manifest: ConnectorCertificationManifest,
    observation: LiveProbeObservation,
    *,
    operator_allowed_hosts: tuple[str, ...],
) -> dict[str, Any]:
    parsed = urlsplit(observation.endpoint_url)
    if parsed.query:
        raise ValueError("live probe endpoint metadata must not include a query string")
    host, port = _canonical_endpoint_descriptor(
        manifest,
        observation.endpoint_url,
        operator_allowed_hosts=operator_allowed_hosts,
    )
    if observation.api_version != manifest.transport.api_version:
        raise ValueError("live probe API version does not match the manifest")
    if observation.auth_mode not in manifest.auth.modes:
        raise ValueError("live probe auth mode does not match the manifest")
    scopes = _exact_scopes(manifest, observation.granted_scopes)
    if observation.read_only is not True or observation.passed is not True:
        raise ValueError("live probe did not demonstrate a passing read-only request")
    if (
        isinstance(observation.status_code, bool)
        or not isinstance(observation.status_code, int)
        or not 200 <= observation.status_code < 300
    ):
        raise ValueError("live probe did not return a successful HTTP status")
    if (
        isinstance(observation.response_bytes, bool)
        or not isinstance(observation.response_bytes, int)
        or not 0 <= observation.response_bytes <= manifest.transport.max_response_bytes
    ):
        raise ValueError("live probe response exceeded the byte contract")
    if (
        isinstance(observation.elapsed_ms, bool)
        or not isinstance(observation.elapsed_ms, (int, float))
        or not math.isfinite(float(observation.elapsed_ms))
        or not 0 <= float(observation.elapsed_ms) <= manifest.transport.deadline_seconds * 1000
    ):
        raise ValueError("live probe exceeded the deadline contract")
    if (
        isinstance(observation.pages, bool)
        or not isinstance(observation.pages, int)
        or not 1 <= observation.pages <= manifest.transport.max_pages
    ):
        raise ValueError("live probe exceeded the pagination contract")
    if not isinstance(observation.cursor_observed, bool):
        raise ValueError("live probe cursor observation must be boolean")
    if observation.detail_code != "probe_passed":
        raise ValueError("live probe detail code must indicate a passing probe")
    rate_allowed = {
        "status_code",
        "throttled",
        "retry_after_seconds",
        "limit",
        "remaining",
        "reset_at",
    }
    if any(key not in rate_allowed for key in observation.rate_limit):
        raise ValueError("live probe rate-limit metadata contains forbidden fields")
    normalized_rate = normalize_rate_limit(
        observation.status_code,
        {
            "retry-after": observation.rate_limit.get("retry_after_seconds"),
            "x-ratelimit-limit": observation.rate_limit.get("limit"),
            "x-ratelimit-remaining": observation.rate_limit.get("remaining"),
            "x-ratelimit-reset": observation.rate_limit.get("reset_at"),
        },
    )
    return {
        "endpoint_host": host,
        "endpoint_port": port,
        "api_version": manifest.transport.api_version,
        "auth_mode": observation.auth_mode,
        "granted_scopes": list(scopes),
        "status_code": observation.status_code,
        "response_bytes": observation.response_bytes,
        "elapsed_ms": float(observation.elapsed_ms),
        "pages": observation.pages,
        "cursor_observed": observation.cursor_observed,
        "rate_limit": normalized_rate,
        "read_only": True,
        "passed": True,
        "detail_code": observation.detail_code,
        "raw_persisted": False,
        "credentials_persisted": False,
    }


def _validate_signature_shape(signature: Mapping[str, str]) -> dict[str, str]:
    if set(signature) != {"alg", "key_id", "pubkey", "sig"}:
        raise ValueError("receipt signer returned an invalid signature envelope")
    alg = str(signature.get("alg", "")).lower()
    pubkey = str(signature.get("pubkey", ""))
    sig = str(signature.get("sig", ""))
    if alg != "ed25519" or not _HEX_32_RE.fullmatch(pubkey) or not _HEX_SIG_RE.fullmatch(sig):
        raise ValueError("receipt signer returned malformed Ed25519 material")
    expected_key_id = hashlib.sha256(bytes.fromhex(pubkey)).hexdigest()[:16]
    if str(signature.get("key_id", "")) != expected_key_id:
        raise ValueError("receipt signer key id does not match its public key")
    return {"alg": alg, "key_id": expected_key_id, "pubkey": pubkey.lower(), "sig": sig.lower()}


def run_live_probe(
    name: str,
    probe: LiveProbe,
    *,
    signer: ReceiptSigner,
    operator_allowed_hosts: tuple[str, ...] = (),
    now: float | None = None,
    ttl_seconds: float = 24 * 3600,
    transport_binding: TransportBinding | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a metadata-only probe and return a signed, untrusted receipt.

    The returned artifact becomes a certification signal only after
    :func:`verify_live_receipt` validates it against a separately configured
    trusted public key *and* the exact registered runtime transport binding.
    Omitting ``transport_binding`` retains the v1 artifact API, but that legacy
    receipt is intentionally ineligible for live runtime authorization.
    """
    if not callable(probe) or not callable(signer):
        raise TypeError("live probe and receipt signer must be callable")
    manifest = certification_manifest(name)
    contracts = run_contract_tests(name)
    if not contracts.passed:
        raise RuntimeError("live probe cannot run while deterministic contracts fail")
    issued_at = float(time.time() if now is None else now)
    ttl = float(ttl_seconds)
    if not math.isfinite(issued_at) or not math.isfinite(ttl):
        raise ValueError("receipt timestamps must be finite")
    if not 60 <= ttl <= _MAX_RECEIPT_TTL_SECONDS:
        raise ValueError("live receipt TTL must be between 60 seconds and 7 days")
    observation = _observation_from(probe(manifest))
    summary = _validated_observation(
        manifest,
        observation,
        operator_allowed_hosts=operator_allowed_hosts,
    )
    binding = (
        None
        if transport_binding is None
        else TransportBinding.from_dict(transport_binding)
    )
    if binding is not None and (
        binding.connector != manifest.connector
        or binding.endpoint_host != summary["endpoint_host"]
        or binding.endpoint_port != summary["endpoint_port"]
        or binding.api_version != summary["api_version"]
        or binding.auth_mode != summary["auth_mode"]
        or list(binding.granted_scopes) != summary["granted_scopes"]
    ):
        raise ValueError("live probe does not match the runtime transport binding")
    receipt_version = RECEIPT_VERSION if binding is not None else LEGACY_RECEIPT_VERSION
    receipt_observation = dict(summary)
    if binding is None:
        receipt_observation["minimum_scopes"] = list(manifest.auth.minimum_scopes)
        del receipt_observation["granted_scopes"]
    receipt: dict[str, Any] = {
        "kind": "lightwork-connector-live-receipt",
        "receipt_version": receipt_version,
        "connector": manifest.connector,
        "manifest_sha256": manifest.digest,
        "lightwork_version": LIGHTWORK_VERSION,
        "contract_test_hashes": list(manifest.test_hashes),
        "issued_at": issued_at,
        "expires_at": issued_at + ttl,
        "observation": receipt_observation,
    }
    probe_material: dict[str, Any] = {
        "connector": manifest.connector,
        "issued_at": issued_at,
        "observation": receipt_observation,
    }
    if binding is not None:
        receipt["transport_binding"] = binding.to_dict()
        receipt["transport_binding_sha256"] = binding.digest
        probe_material["transport_binding_sha256"] = binding.digest
    receipt["probe_id"] = _digest(probe_material)[:32]
    payload = _canonical(receipt)
    raw_signature = signer(payload)
    if not isinstance(raw_signature, Mapping):
        raise TypeError("receipt signer must return a signature envelope")
    signature = _validate_signature_shape(raw_signature)
    try:
        from ..audit.signing import verify_ed25519

        signature_valid = verify_ed25519(signature["pubkey"], signature["sig"], payload)
    except Exception:
        signature_valid = False
    if not signature_valid:
        raise ValueError("receipt signer signature does not cover the receipt payload")
    receipt["signature"] = signature
    return receipt


def _receipt_observation_reason(
    manifest: ConnectorCertificationManifest,
    observation: Mapping[str, Any],
    *,
    receipt_version: int,
    operator_allowed_hosts: tuple[str, ...],
) -> str | None:
    scope_key = (
        "minimum_scopes"
        if receipt_version == LEGACY_RECEIPT_VERSION
        else "granted_scopes"
    )
    expected = {
        "endpoint_host",
        "endpoint_port",
        "api_version",
        "auth_mode",
        scope_key,
        "status_code",
        "response_bytes",
        "elapsed_ms",
        "pages",
        "cursor_observed",
        "rate_limit",
        "read_only",
        "passed",
        "detail_code",
        "raw_persisted",
        "credentials_persisted",
    }
    if set(observation) != expected:
        return "receipt_observation_invalid"
    endpoint_host = str(observation.get("endpoint_host", ""))
    endpoint_port = observation.get("endpoint_port")
    if isinstance(endpoint_port, bool) or not isinstance(endpoint_port, int):
        return "receipt_endpoint_invalid"
    if not host_allowed(
        manifest,
        f"https://{endpoint_host}:{endpoint_port}",
        operator_allowed_hosts=operator_allowed_hosts,
    ):
        return "receipt_endpoint_untrusted"
    if (
        observation.get("api_version") != manifest.transport.api_version
        or observation.get("auth_mode") not in manifest.auth.modes
        or observation.get(scope_key)
        != (
            list(manifest.auth.minimum_scopes)
            if receipt_version == LEGACY_RECEIPT_VERSION
            else sorted(manifest.auth.minimum_scopes)
        )
        or observation.get("read_only") is not True
        or observation.get("passed") is not True
        or observation.get("raw_persisted") is not False
        or observation.get("credentials_persisted") is not False
    ):
        return "receipt_contract_mismatch"
    status_code = observation.get("status_code")
    response_bytes = observation.get("response_bytes")
    elapsed_ms = observation.get("elapsed_ms")
    pages = observation.get("pages")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not 200 <= status_code < 300
    ):
        return "receipt_probe_failed"
    if (
        isinstance(response_bytes, bool)
        or not isinstance(response_bytes, int)
        or not 0 <= response_bytes <= manifest.transport.max_response_bytes
    ):
        return "receipt_contract_mismatch"
    if (
        isinstance(elapsed_ms, bool)
        or not isinstance(elapsed_ms, (int, float))
        or not math.isfinite(float(elapsed_ms))
        or not 0 <= float(elapsed_ms) <= manifest.transport.deadline_seconds * 1000
    ):
        return "receipt_contract_mismatch"
    if (
        isinstance(pages, bool)
        or not isinstance(pages, int)
        or not 1 <= pages <= manifest.transport.max_pages
        or not isinstance(observation.get("cursor_observed"), bool)
        or observation.get("detail_code") != "probe_passed"
    ):
        return "receipt_contract_mismatch"
    rate_limit = observation.get("rate_limit")
    expected_rate_keys = {
        "status_code",
        "throttled",
        "retry_after_seconds",
        "limit",
        "remaining",
        "reset_at",
    }
    if not isinstance(rate_limit, Mapping) or set(rate_limit) != expected_rate_keys:
        return "receipt_rate_limit_invalid"
    if rate_limit.get("status_code") != status_code or rate_limit.get("throttled") is not False:
        return "receipt_rate_limit_invalid"
    for key in ("retry_after_seconds", "limit", "remaining", "reset_at"):
        value = rate_limit.get(key)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            return "receipt_rate_limit_invalid"
    return None


def _receipt_transport_binding_reason(
    manifest: ConnectorCertificationManifest,
    receipt_binding: object,
    receipt_binding_sha256: object,
    observation: Mapping[str, Any],
    *,
    expected_binding: TransportBinding | Mapping[str, Any] | None,
    operator_allowed_hosts: tuple[str, ...],
) -> str | None:
    try:
        bound = TransportBinding.from_dict(receipt_binding)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "receipt_transport_binding_invalid"
    if receipt_binding_sha256 != bound.digest:
        return "receipt_transport_binding_digest_invalid"
    if bound.connector != manifest.connector:
        return "receipt_transport_binding_mismatch"
    if not host_allowed(
        manifest,
        f"https://{bound.endpoint_host}:{bound.endpoint_port}",
        operator_allowed_hosts=operator_allowed_hosts,
    ):
        return "receipt_endpoint_untrusted"
    if (
        bound.api_version != manifest.transport.api_version
        or bound.auth_mode not in manifest.auth.modes
        or bound.granted_scopes != tuple(sorted(manifest.auth.minimum_scopes))
        or bound.endpoint_host != observation.get("endpoint_host")
        or bound.endpoint_port != observation.get("endpoint_port")
        or bound.api_version != observation.get("api_version")
        or bound.auth_mode != observation.get("auth_mode")
        or list(bound.granted_scopes) != observation.get("granted_scopes")
    ):
        return "receipt_transport_binding_mismatch"
    if expected_binding is None:
        return "expected_transport_binding_required"
    try:
        expected = TransportBinding.from_dict(expected_binding)
    except (TypeError, ValueError):
        return "expected_transport_binding_invalid"
    if bound != expected:
        return "receipt_transport_binding_mismatch"
    return None


def verify_live_receipt(  # noqa: C901 -- explicit fail-closed schema checks stay linear
    receipt: Mapping[str, Any] | None,
    *,
    trusted_pubkey_hex: str | None,
    now: float | None = None,
    operator_allowed_hosts: tuple[str, ...] = (),
    expected_binding: TransportBinding | Mapping[str, Any] | None = None,
) -> ReceiptVerification:
    """Fail closed on unsigned, untrusted, tampered, mismatched, or stale receipts."""
    if not isinstance(receipt, Mapping):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_missing")
    trusted = str(trusted_pubkey_hex or "").strip().lower()
    if not _HEX_32_RE.fullmatch(trusted):
        return ReceiptVerification(False, CONTRACT_TESTED, "trusted_pubkey_required")
    try:
        encoded = _canonical(receipt)
    except (TypeError, ValueError):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_not_canonical_json")
    if len(encoded) > _MAX_RECEIPT_BYTES:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_too_large")
    receipt_version = receipt.get("receipt_version")
    if (
        receipt.get("kind") != "lightwork-connector-live-receipt"
        or isinstance(receipt_version, bool)
        or receipt_version not in (LEGACY_RECEIPT_VERSION, RECEIPT_VERSION)
    ):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_version_invalid")
    expected_top = {
        "kind",
        "receipt_version",
        "connector",
        "manifest_sha256",
        "lightwork_version",
        "contract_test_hashes",
        "issued_at",
        "expires_at",
        "probe_id",
        "observation",
        "signature",
    }
    if receipt_version == RECEIPT_VERSION:
        expected_top.update({"transport_binding", "transport_binding_sha256"})
    if set(receipt) != expected_top:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_schema_invalid")
    try:
        manifest = certification_manifest(str(receipt.get("connector", "")))
    except KeyError:
        return ReceiptVerification(False, DECLARED, "connector_not_in_certified_core")
    if not run_contract_tests(manifest.connector).passed:
        return ReceiptVerification(False, DECLARED, "deterministic_contract_tests_failed")
    if receipt.get("manifest_sha256") != manifest.digest:
        return ReceiptVerification(False, CONTRACT_TESTED, "manifest_digest_mismatch")
    if receipt.get("lightwork_version") != LIGHTWORK_VERSION:
        return ReceiptVerification(False, CONTRACT_TESTED, "lightwork_version_mismatch")
    if receipt.get("contract_test_hashes") != list(manifest.test_hashes):
        return ReceiptVerification(False, CONTRACT_TESTED, "contract_test_hash_mismatch")
    current = float(time.time() if now is None else now)
    if isinstance(receipt["issued_at"], bool) or isinstance(receipt["expires_at"], bool):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_timestamp_invalid")
    try:
        issued = float(receipt["issued_at"])
        expires = float(receipt["expires_at"])
    except (TypeError, ValueError):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_timestamp_invalid")
    if not all(math.isfinite(value) for value in (current, issued, expires)):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_timestamp_invalid")
    if issued > current + _CLOCK_SKEW_SECONDS:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_from_future")
    if expires <= current:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_stale")
    if expires <= issued or expires - issued > _MAX_RECEIPT_TTL_SECONDS:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_ttl_invalid")
    observation = receipt.get("observation")
    if not isinstance(observation, Mapping):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_observation_invalid")
    observation_reason = _receipt_observation_reason(
        manifest,
        observation,
        receipt_version=receipt_version,
        operator_allowed_hosts=operator_allowed_hosts,
    )
    if observation_reason:
        return ReceiptVerification(False, CONTRACT_TESTED, observation_reason)
    probe_material: dict[str, Any] = {
        "connector": manifest.connector,
        "issued_at": issued,
        "observation": observation,
    }
    binding_reason = "receipt_transport_binding_required"
    if receipt_version == RECEIPT_VERSION:
        probe_material["transport_binding_sha256"] = receipt.get(
            "transport_binding_sha256"
        )
        binding_reason = _receipt_transport_binding_reason(
            manifest,
            receipt.get("transport_binding"),
            receipt.get("transport_binding_sha256"),
            observation,
            expected_binding=expected_binding,
            operator_allowed_hosts=operator_allowed_hosts,
        )
    expected_probe_id = _digest(probe_material)[:32]
    if receipt.get("probe_id") != expected_probe_id:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_probe_id_invalid")
    signature = receipt.get("signature")
    if not isinstance(signature, Mapping):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_unsigned")
    if str(signature.get("pubkey", "")).lower() != trusted:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_signer_untrusted")
    try:
        shaped = _validate_signature_shape(signature)
    except (TypeError, ValueError):
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_signature_invalid")
    payload = _canonical({key: value for key, value in receipt.items() if key != "signature"})
    try:
        from ..audit.signing import verify_ed25519

        valid = verify_ed25519(trusted, shaped["sig"], payload)
    except Exception:
        valid = False
    if not valid:
        return ReceiptVerification(False, CONTRACT_TESTED, "receipt_signature_invalid")
    if binding_reason:
        return ReceiptVerification(False, CONTRACT_TESTED, binding_reason, shaped["key_id"])
    return ReceiptVerification(True, LIVE_CERTIFIED, "live_receipt_verified", shaped["key_id"])


def assess_connector(
    name: str,
    *,
    receipt: Mapping[str, Any] | None = None,
    trusted_pubkey_hex: str | None = None,
    now: float | None = None,
    operator_allowed_hosts: tuple[str, ...] = (),
    expected_binding: TransportBinding | Mapping[str, Any] | None = None,
) -> CertificationAssessment:
    manifest = certification_manifest(name)
    contracts = run_contract_tests(name)
    if not contracts.passed:
        return CertificationAssessment(
            manifest.connector,
            DECLARED,
            manifest.digest,
            False,
            False,
            "deterministic_contract_tests_failed",
        )
    verification = verify_live_receipt(
        receipt,
        trusted_pubkey_hex=trusted_pubkey_hex,
        now=now,
        operator_allowed_hosts=operator_allowed_hosts,
        expected_binding=expected_binding,
    )
    return CertificationAssessment(
        manifest.connector,
        verification.status if verification.valid else CONTRACT_TESTED,
        manifest.digest,
        True,
        verification.valid,
        verification.reason,
    )


def _record_override(audit_recorder: AuditRecorder | None, payload: dict[str, Any]) -> bool:
    if audit_recorder is not None:
        try:
            return bool(audit_recorder("env_hunt_connector_certification_override", payload))
        except Exception:  # failure-policy: fail_closed
            return False
    try:
        from ..audit import record

        return bool(record("env_hunt_connector_certification_override", **payload))
    except Exception:  # failure-policy: fail_closed
        return False


def decide_connector_use(
    name: str,
    *,
    enterprise: bool,
    receipt: Mapping[str, Any] | None = None,
    trusted_pubkey_hex: str | None = None,
    operator_allowed_hosts: tuple[str, ...] = (),
    expected_binding: TransportBinding | Mapping[str, Any] | None = None,
    allow_uncertified: bool = False,
    actor: str = "",
    reason: str = "",
    audit_recorder: AuditRecorder | None = None,
    now: float | None = None,
) -> ConnectorUseDecision:
    """Apply certified-only enterprise policy with a fail-closed audited override."""
    assessment = assess_connector(
        name,
        receipt=receipt,
        trusted_pubkey_hex=trusted_pubkey_hex,
        now=now,
        operator_allowed_hosts=operator_allowed_hosts,
        expected_binding=expected_binding,
    )
    if not isinstance(enterprise, bool):
        return ConnectorUseDecision(name, False, assessment.status, "enterprise_policy_invalid")
    if assessment.status == LIVE_CERTIFIED:
        return ConnectorUseDecision(name, True, assessment.status, "live_certification_required")
    if enterprise is not True:
        return ConnectorUseDecision(
            name,
            assessment.status == CONTRACT_TESTED,
            assessment.status,
            "contract_tested_allowed_outside_enterprise",
        )
    if allow_uncertified is not True:
        return ConnectorUseDecision(name, False, assessment.status, "enterprise_requires_live_receipt")
    normalized_actor = str(actor).strip()
    normalized_reason = str(reason).strip()
    if (
        not _IDENTIFIER_RE.fullmatch(normalized_actor)
        or not normalized_reason
        or len(normalized_reason) > 2000
    ):
        return ConnectorUseDecision(name, False, assessment.status, "override_identity_or_reason_invalid")
    payload = {
        "connector": assessment.connector,
        "certification_status": assessment.status,
        "manifest_sha256": assessment.manifest_sha256,
        "actor": normalized_actor,
        "reason_sha256": hashlib.sha256(normalized_reason.encode("utf-8")).hexdigest(),
        "allow_uncertified": True,
        "live_verification_reason": assessment.reason,
    }
    if not _record_override(audit_recorder, payload):
        return ConnectorUseDecision(name, False, assessment.status, "override_audit_rejected")
    return ConnectorUseDecision(
        name,
        True,
        assessment.status,
        "explicit_uncertified_override",
        override_audited=True,
    )


def certification_policy(
    config: Mapping[str, Any] | None = None,
    *,
    enterprise: bool | None = None,
) -> CertificationPolicy:
    """Resolve strict connector policy; enterprise always forces certified-only."""
    degraded = False
    if config is None:
        try:
            from ..config import config_source_errors, load_global_config

            config = load_global_config()
            degraded = bool(config_source_errors(include_tenant=False))
        except Exception:  # failure-policy: fail_closed when enterprise is possible
            config = {}
            degraded = True
    root = config if isinstance(config, Mapping) else {}
    try:
        from ..enterprise import enterprise_enabled

        deployed_enterprise = bool(enterprise_enabled())
    except Exception:  # failure-policy: fail_closed
        deployed_enterprise = True
        degraded = True
    if enterprise is not None and not isinstance(enterprise, bool):
        degraded = True
    # An injected hint may raise posture for tests or an embedding, but it can
    # never lower the posture reported by the actual deployment.
    enterprise_mode = deployed_enterprise or enterprise is True
    connectors = root.get("connectors")
    connectors = connectors if isinstance(connectors, Mapping) else {}
    section = connectors.get("certification")
    if section is not None and not isinstance(section, Mapping):
        section = {}
        degraded = True
    section = section if isinstance(section, Mapping) else {}
    explicit = section.get("certified_only")
    if explicit is not None and not isinstance(explicit, bool):
        degraded = True
    certified_only = enterprise_mode or explicit is True
    allow_value = section.get("allow_uncertified", False)
    if not isinstance(allow_value, bool):
        degraded = True
    allow_uncertified = allow_value is True and not degraded
    if degraded and enterprise_mode:
        certified_only = True
        allow_uncertified = False
    return CertificationPolicy(
        certified_only=certified_only,
        allow_uncertified=allow_uncertified,
        enterprise=enterprise_mode,
        config_degraded=degraded,
    )


def decide_configured_connector_use(
    name: str,
    *,
    config: Mapping[str, Any] | None = None,
    enterprise: bool | None = None,
    receipt: Mapping[str, Any] | None = None,
    trusted_pubkey_hex: str | None = None,
    operator_allowed_hosts: tuple[str, ...] = (),
    expected_binding: TransportBinding | Mapping[str, Any] | None = None,
    actor: str = "",
    reason: str = "",
    audit_recorder: AuditRecorder | None = None,
    now: float | None = None,
) -> ConnectorUseDecision:
    """Apply active config without letting an enterprise opt out of the floor."""
    policy = certification_policy(config, enterprise=enterprise)
    if policy.config_degraded and policy.enterprise:
        return ConnectorUseDecision(name, False, DECLARED, "certification_config_untrusted")
    if not policy.certified_only:
        return decide_connector_use(
            name,
            enterprise=False,
            receipt=receipt,
            trusted_pubkey_hex=trusted_pubkey_hex,
            operator_allowed_hosts=operator_allowed_hosts,
            expected_binding=expected_binding,
            now=now,
        )
    return decide_connector_use(
        name,
        enterprise=True,
        receipt=receipt,
        trusted_pubkey_hex=trusted_pubkey_hex,
        operator_allowed_hosts=operator_allowed_hosts,
        expected_binding=expected_binding,
        allow_uncertified=policy.allow_uncertified,
        actor=actor,
        reason=reason,
        audit_recorder=audit_recorder,
        now=now,
    )


__all__ = [
    "CERTIFIED_CORE_MANIFESTS",
    "CONTRACT_TESTED",
    "DECLARED",
    "LIVE_CERTIFIED",
    "AuthContract",
    "CertificationAssessment",
    "CertificationPolicy",
    "ConnectorCertificationManifest",
    "ConnectorUseDecision",
    "ContractCheck",
    "ContractTestResult",
    "ContractTestSpec",
    "LiveProbe",
    "LiveProbeObservation",
    "ReceiptVerification",
    "TransportBinding",
    "TransportContract",
    "assess_connector",
    "build_transport_binding",
    "callable_implementation_digest",
    "certification_manifest",
    "certification_manifests",
    "certification_policy",
    "decide_configured_connector_use",
    "decide_connector_use",
    "host_allowed",
    "normalize_rate_limit",
    "run_contract_tests",
    "run_live_probe",
    "verify_live_receipt",
]
