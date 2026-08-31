"""Closed read-only provider contracts for third-party intelligence feeds.

The adapters in this module are transport contracts, not proof of a live
provider integration.  A deployment injects a trusted GET-only transport and
an opaque credential binding.  Source metadata chooses only one of the closed
provider IDs below; it can never supply a URL, headers, method, or request
payload.  Raw provider pages and credential material are validated in memory
and are never returned in the normalized result.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

from .paths import canonical_tenant_id

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_CURSOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+=-]{0,255}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_PAGE_BYTES = 512 * 1024
_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_MAX_PAGES = 32
_MAX_PAGE_RECORDS = 500
_MAX_TOTAL_RECORDS = 5_000
_MAX_CITATIONS = 100

_PROVIDER_CONTRACTS: dict[str, tuple[str, str, str]] = {
    "riskrecon": (
        "api.riskrecon.com",
        "/api/v1/third-party-observations",
        "lightwork.riskrecon-observation-page.v1",
    ),
    "securityscorecard": (
        "api.securityscorecard.io",
        "/companies/observations",
        "lightwork.securityscorecard-observation-page.v1",
    ),
    "hacknotice": (
        "api.hacknotice.com",
        "/v1/vendor-observations",
        "lightwork.hacknotice-observation-page.v1",
    ),
}


class ThirdPartyProviderError(RuntimeError):
    """Base provider-boundary error."""


class ThirdPartyProviderConfigError(ThirdPartyProviderError):
    """A trusted transport, provider, or credential binding is unavailable."""


class ThirdPartyProviderProtocolError(ThirdPartyProviderError):
    """A provider returned content outside its closed contract."""


class ThirdPartyProviderIncompleteError(ThirdPartyProviderError):
    """A provider explicitly reported incomplete coverage."""


class ThirdPartyProviderTransport(Protocol):
    """Trusted GET-only transport; implementations own auth/header handling."""

    def get_json(
        self,
        *,
        host: str,
        path: str,
        query: Mapping[str, str],
        credential: object,
        maximum_response_bytes: int,  # noqa
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    """Opaque transient credential plus the stable key authority it represents."""

    key_id: str
    secret: object = field(repr=False)

    def __post_init__(self) -> None:
        _identifier(self.key_id, "credential key_id")
        if self.secret is None:
            raise ThirdPartyProviderConfigError("credential binding has no secret")


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    tenant_id: str
    provider: str
    source_id: str
    source_revision: int
    source_integrity_sha256: str
    kind: str
    capability: str
    key_id: str

    def __post_init__(self) -> None:
        try:
            canonical_tenant = canonical_tenant_id(self.tenant_id)
        except (TypeError, ValueError) as exc:
            raise ThirdPartyProviderProtocolError("tenant_id is invalid") from exc
        if canonical_tenant != self.tenant_id:
            raise ThirdPartyProviderProtocolError("tenant_id must already be canonical")
        _identifier(self.provider, "provider")
        _identifier(self.source_id, "source_id")
        _integer(self.source_revision, "source_revision", 1, 2**31 - 1)
        _digest(self.source_integrity_sha256, "source_integrity_sha256")
        _identifier(self.kind, "kind")
        if self.capability != "monitoring":
            raise ThirdPartyProviderConfigError("provider capability is not monitoring")
        _identifier(self.key_id, "key_id")


@dataclass(frozen=True, slots=True)
class ProviderCitation:
    citation_id: str
    title: str
    uri: str
    pinpoint: str


@dataclass(frozen=True, slots=True)
class ProviderFeedResult:
    provider: str
    source_id: str
    source_revision: int
    source_integrity_sha256: str
    capability: str
    key_id_sha256: str
    kind: str
    dataset_revision: str
    observed_at: float
    valid_until: float
    record_count: int
    cursor_count: int
    coverage_complete: bool
    observation_sha256: str
    evidence_sha256: str
    provider_receipt_sha256: str
    citations: tuple[ProviderCitation, ...]


def _canonical(value: object, *, maximum: int = _MAX_PAGE_BYTES) -> bytes:
    try:
        result = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ThirdPartyProviderProtocolError("provider page is not finite canonical JSON") from exc
    if not 1 <= len(result) <= maximum:
        raise ThirdPartyProviderProtocolError("provider page exceeds its byte bound")
    return result


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value, maximum=_MAX_TOTAL_BYTES)).hexdigest()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ThirdPartyProviderProtocolError(f"{label} is not a bounded identifier")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ThirdPartyProviderProtocolError(f"{label} is not a SHA-256 digest")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ThirdPartyProviderProtocolError(f"{label} is outside its integer bound")
    return value


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) < 100_000_000_000.0
    ):
        raise ThirdPartyProviderProtocolError(f"{label} is not a finite timestamp")
    return float(value)


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ThirdPartyProviderProtocolError(f"{label} must be text")
    result = value.strip()
    if not result or len(result.encode("utf-8")) > maximum:
        raise ThirdPartyProviderProtocolError(f"{label} is outside its text bound")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise ThirdPartyProviderProtocolError(f"{label} contains control characters")
    return result


def _https_uri(value: object, label: str) -> str:
    uri = _text(value, label, 2_048)
    parsed = urlparse(uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ThirdPartyProviderProtocolError(f"{label} is not credential-free HTTPS")
    return uri


def _citation(value: object) -> ProviderCitation:
    if not isinstance(value, Mapping) or set(value) != {
        "citation_id",
        "title",
        "uri",
        "pinpoint",
    }:
        raise ThirdPartyProviderProtocolError("provider citation schema is closed")
    uri = _https_uri(value["uri"], "citation uri")
    if urlparse(uri).query:
        raise ThirdPartyProviderProtocolError(
            "provider citation URI must not contain query parameters"
        )
    return ProviderCitation(
        citation_id=_identifier(value["citation_id"], "citation_id"),
        title=_text(value["title"], "citation title", 500),
        uri=uri,
        pinpoint=_text(value["pinpoint"], "citation pinpoint", 500),
    )


class FixedHostThirdPartyFeedAdapter:
    """Parse one named provider's fixed-host, GET-only digest feed."""

    def __init__(
        self,
        *,
        provider: str,
        host: str,
        path: str,
        schema: str,
        transport: ThirdPartyProviderTransport,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.provider = _identifier(provider, "provider")
        self.host = _text(host, "provider host", 253).lower()
        self.path = _text(path, "provider path", 500)
        self.schema = _text(schema, "provider schema", 200)
        if not self.path.startswith("/") or "?" in self.path or "#" in self.path:
            raise ThirdPartyProviderConfigError("provider path must be a fixed absolute path")
        if not callable(getattr(transport, "get_json", None)):
            raise ThirdPartyProviderConfigError("trusted GET transport is unavailable")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 1.0 <= float(timeout_seconds) <= 120.0
        ):
            raise ThirdPartyProviderConfigError("provider timeout is outside its bound")
        self._transport = transport
        self._timeout_seconds = float(timeout_seconds)

    def collect(
        self,
        request: ProviderRequest,
        credential: CredentialBinding,
    ) -> ProviderFeedResult:
        if request.provider != self.provider or request.key_id != credential.key_id:
            raise ThirdPartyProviderConfigError("provider request binding is inconsistent")
        cursor: str | None = None
        cursors: set[str] = set()
        records: list[dict[str, Any]] = []
        record_ids: set[str] = set()
        citations: dict[str, ProviderCitation] = {}
        page_receipts: list[str] = []
        total_bytes = 0
        dataset_revision: str | None = None
        observed_at: float | None = None
        valid_until: float | None = None
        for _page_number in range(1, _MAX_PAGES + 1):
            query = {
                "tenant_id": request.tenant_id,
                "provider": request.provider,
                "source_id": request.source_id,
                "source_revision": str(request.source_revision),
                "source_integrity_sha256": request.source_integrity_sha256,
                "capability": request.capability,
                "key_id": request.key_id,
                "kind": request.kind,
            }
            if cursor is not None:
                query["cursor"] = cursor
            try:
                raw = self._transport.get_json(
                    host=self.host,
                    path=self.path,
                    query=query,
                    credential=credential.secret,
                    maximum_response_bytes=_MAX_PAGE_BYTES,
                    timeout_seconds=self._timeout_seconds,
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except ThirdPartyProviderError:
                raise
            except Exception:
                raise ThirdPartyProviderError("provider GET transport failed") from None
            if not isinstance(raw, Mapping):
                raise ThirdPartyProviderProtocolError("provider page must be an object")
            page_bytes = _canonical(raw)
            total_bytes += len(page_bytes)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise ThirdPartyProviderProtocolError("provider feed exceeds its byte bound")
            parsed = self._parse_page(raw, request)
            if dataset_revision is None:
                dataset_revision = parsed["dataset_revision"]
                observed_at = parsed["observed_at"]
                valid_until = parsed["valid_until"]
            elif (
                parsed["dataset_revision"] != dataset_revision
                or parsed["observed_at"] != observed_at
                or parsed["valid_until"] != valid_until
            ):
                raise ThirdPartyProviderProtocolError("provider feed drifted across pages")
            for record in parsed["records"]:
                record_id = record["record_id"]
                if record_id in record_ids:
                    raise ThirdPartyProviderProtocolError("provider record IDs repeat")
                record_ids.add(record_id)
                records.append(record)
                citation = record["citation"]
                prior = citations.get(citation.citation_id)
                if prior is not None and prior != citation:
                    raise ThirdPartyProviderProtocolError("provider citation ID drifted")
                citations[citation.citation_id] = citation
            if len(records) > _MAX_TOTAL_RECORDS or len(citations) > _MAX_CITATIONS:
                raise ThirdPartyProviderProtocolError("provider feed exceeds its item bound")
            page_receipts.append(hashlib.sha256(page_bytes).hexdigest())
            next_cursor = self._continuation(parsed)
            if next_cursor is None:
                break
            if next_cursor in cursors:
                raise ThirdPartyProviderProtocolError("provider cursor cycle detected")
            cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise ThirdPartyProviderProtocolError("provider page bound was exhausted")
        if not records or dataset_revision is None or observed_at is None or valid_until is None:
            raise ThirdPartyProviderIncompleteError("provider returned no explicit observations")
        ordered = sorted(records, key=lambda item: item["record_id"])
        observation = _sha256(
            [
                {
                    "record_id": item["record_id"],
                    "observation_sha256": item["observation_sha256"],
                }
                for item in ordered
            ]
        )
        evidence = _sha256(
            [
                {
                    "record_id": item["record_id"],
                    "evidence_sha256": item["evidence_sha256"],
                }
                for item in ordered
            ]
        )
        receipt = _sha256(
            {
                "request": {
                    "tenant_id": request.tenant_id,
                    "provider": request.provider,
                    "source_id": request.source_id,
                    "source_revision": request.source_revision,
                    "source_integrity_sha256": request.source_integrity_sha256,
                    "capability": request.capability,
                    "key_id_sha256": hashlib.sha256(request.key_id.encode()).hexdigest(),
                    "kind": request.kind,
                },
                "dataset_revision": dataset_revision,
                "observed_at": observed_at,
                "valid_until": valid_until,
                "record_count": len(ordered),
                "page_receipts": page_receipts,
                "observation_sha256": observation,
                "evidence_sha256": evidence,
            }
        )
        return ProviderFeedResult(
            provider=request.provider,
            source_id=request.source_id,
            source_revision=request.source_revision,
            source_integrity_sha256=request.source_integrity_sha256,
            capability=request.capability,
            key_id_sha256=hashlib.sha256(request.key_id.encode()).hexdigest(),
            kind=request.kind,
            dataset_revision=dataset_revision,
            observed_at=observed_at,
            valid_until=valid_until,
            record_count=len(ordered),
            cursor_count=len(page_receipts),
            coverage_complete=True,
            observation_sha256=observation,
            evidence_sha256=evidence,
            provider_receipt_sha256=receipt,
            citations=tuple(citations[key] for key in sorted(citations)),
        )

    @staticmethod
    def _continuation(parsed: Mapping[str, Any]) -> str | None:
        next_cursor = parsed["next_cursor"]
        reasons = parsed["incomplete_reason_codes"]
        if parsed["complete"]:
            if next_cursor is not None or reasons:
                raise ThirdPartyProviderProtocolError(
                    "complete provider page has contradictory coverage markers"
                )
            return None
        if reasons:
            raise ThirdPartyProviderIncompleteError(reasons[0])
        if next_cursor is None:
            raise ThirdPartyProviderIncompleteError("provider_reported_incomplete")
        return str(next_cursor)

    def _parse_page(
        self,
        raw: Mapping[str, object],
        request: ProviderRequest,
    ) -> dict[str, Any]:
        expected_keys = {
            "schema",
            "tenant_id",
            "provider",
            "source_id",
            "source_revision",
            "source_integrity_sha256",
            "capability",
            "key_id",
            "kind",
            "dataset_revision",
            "observed_at",
            "valid_until",
            "complete",
            "incomplete_reason_codes",
            "next_cursor",
            "records",
        }
        if set(raw) != expected_keys or raw.get("schema") != self.schema:
            raise ThirdPartyProviderProtocolError("provider page schema is closed")
        pins = {
            "tenant_id": request.tenant_id,
            "provider": request.provider,
            "source_id": request.source_id,
            "source_revision": request.source_revision,
            "source_integrity_sha256": request.source_integrity_sha256,
            "capability": request.capability,
            "key_id": request.key_id,
            "kind": request.kind,
        }
        if any(raw.get(key) != value for key, value in pins.items()):
            raise ThirdPartyProviderProtocolError("provider response pins do not match")
        dataset = _identifier(raw["dataset_revision"], "dataset_revision")
        observed = _timestamp(raw["observed_at"], "observed_at")
        valid = _timestamp(raw["valid_until"], "valid_until")
        if valid <= observed:
            raise ThirdPartyProviderProtocolError("provider validity window is inverted")
        complete = raw["complete"]
        if not isinstance(complete, bool):
            raise ThirdPartyProviderProtocolError("provider complete marker must be boolean")
        reasons_value = raw["incomplete_reason_codes"]
        if isinstance(reasons_value, (str, bytes)) or not isinstance(reasons_value, Sequence):
            raise ThirdPartyProviderProtocolError("incomplete reason codes must be a list")
        if len(reasons_value) > 20:
            raise ThirdPartyProviderProtocolError("too many incomplete reason codes")
        reasons = tuple(_identifier(item, "incomplete reason code") for item in reasons_value)
        cursor_value = raw["next_cursor"]
        if cursor_value is not None and (
            not isinstance(cursor_value, str) or not _CURSOR_RE.fullmatch(cursor_value)
        ):
            raise ThirdPartyProviderProtocolError("provider cursor is invalid")
        rows = raw["records"]
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise ThirdPartyProviderProtocolError("provider records must be a list")
        if len(rows) > _MAX_PAGE_RECORDS:
            raise ThirdPartyProviderProtocolError("provider page has too many records")
        records: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "record_id",
                "observation_sha256",
                "evidence_sha256",
                "citation",
            }:
                raise ThirdPartyProviderProtocolError("provider record schema is closed")
            records.append(
                {
                    "record_id": _identifier(row["record_id"], "record_id"),
                    "observation_sha256": _digest(row["observation_sha256"], "observation_sha256"),
                    "evidence_sha256": _digest(row["evidence_sha256"], "evidence_sha256"),
                    "citation": _citation(row["citation"]),
                }
            )
        return {
            "dataset_revision": dataset,
            "observed_at": observed,
            "valid_until": valid,
            "complete": complete,
            "incomplete_reason_codes": reasons,
            "next_cursor": cursor_value,
            "records": records,
        }


class ThirdPartyProviderFactory:
    """Build only explicitly enabled, fixed-host provider adapters."""

    def __init__(
        self,
        *,
        transport: ThirdPartyProviderTransport,
        enabled_providers: Sequence[str],
        generic_approved_host: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if isinstance(enabled_providers, (str, bytes)) or not isinstance(
            enabled_providers, Sequence
        ):
            raise ThirdPartyProviderConfigError("enabled providers must be a list")
        enabled = tuple(_identifier(value, "enabled provider") for value in enabled_providers)
        if len(enabled) != len(set(enabled)):
            raise ThirdPartyProviderConfigError("enabled providers contain duplicates")
        known = {*_PROVIDER_CONTRACTS, "generic_approved"}
        if not set(enabled).issubset(known):
            raise ThirdPartyProviderConfigError("enabled provider ID is not supported")
        if "generic_approved" in enabled:
            if generic_approved_host is None:
                raise ThirdPartyProviderConfigError(
                    "generic approved provider requires a trusted fixed host"
                )
            parsed = urlparse(f"https://{generic_approved_host}")
            if (
                not parsed.hostname
                or parsed.hostname != generic_approved_host.lower()
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ThirdPartyProviderConfigError("generic provider host is invalid")
        self._transport = transport
        self._enabled = frozenset(enabled)
        self._generic_host = generic_approved_host.lower() if generic_approved_host else None
        self._timeout_seconds = timeout_seconds

    @property
    def enabled_providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._enabled))

    def create(self, provider: str, provenance_uri: str) -> FixedHostThirdPartyFeedAdapter:
        provider_id = _identifier(provider, "provider")
        if provider_id not in self._enabled:
            raise ThirdPartyProviderConfigError("provider adapter is not enabled")
        if provider_id == "generic_approved":
            if self._generic_host is None:  # pragma: no cover - constructor invariant
                raise ThirdPartyProviderConfigError("generic provider host is unavailable")
            contract = (
                self._generic_host,
                "/v1/third-party-observations",
                "lightwork.generic-approved-observation-page.v1",
            )
        else:
            contract = _PROVIDER_CONTRACTS[provider_id]
        provenance = urlparse(_https_uri(provenance_uri, "source provenance_uri"))
        if provenance.query:
            raise ThirdPartyProviderConfigError(
                "source provenance URI must not contain query parameters"
            )
        if (provenance.hostname or "").lower() != contract[0]:
            raise ThirdPartyProviderConfigError(
                "source provenance host does not match the fixed provider host"
            )
        return FixedHostThirdPartyFeedAdapter(
            provider=provider_id,
            host=contract[0],
            path=contract[1],
            schema=contract[2],
            transport=self._transport,
            timeout_seconds=self._timeout_seconds,
        )


def available_provider_concepts() -> tuple[str, ...]:
    return tuple(sorted({*_PROVIDER_CONTRACTS, "generic_approved"}))


__all__ = [
    "CredentialBinding",
    "FixedHostThirdPartyFeedAdapter",
    "ProviderCitation",
    "ProviderFeedResult",
    "ProviderRequest",
    "ThirdPartyProviderConfigError",
    "ThirdPartyProviderError",
    "ThirdPartyProviderFactory",
    "ThirdPartyProviderIncompleteError",
    "ThirdPartyProviderProtocolError",
    "ThirdPartyProviderTransport",
    "available_provider_concepts",
]
