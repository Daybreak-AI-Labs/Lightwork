"""Strict consent-standards interoperability boundaries.

This module intentionally implements only the portions of external consent
standards that are covered by deterministic vectors here:

* ``Sec-GPC`` and legacy ``DNT`` request-header observation;
* a policy-bound internal projection of US opt-out signals;
* Google Consent Mode v2 ``default`` and ``update`` command mappings; and
* the GPP version-1 *header* codec.

It does **not** claim to be an IAB-certified CMP, a TCF codec, or a GPP
discrete-section codec.  Those unsupported wire operations fail closed.  All
implemented transformations require an exact policy, purpose, vendor-list,
specification, and digest binding supplied by the caller's version authority.
No free-text notice, identity, cookie, IP address, or credential is accepted or
retained.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

SCHEMA_VERSION = 1
BINDING_SCHEMA = "lightwork.consent-standards-binding.v1"
SIGNAL_SCHEMA = "lightwork.browser-privacy-signals.v1"
US_PROJECTION_SCHEMA = "lightwork.us-privacy-projection.v1"
GOOGLE_COMMAND_SCHEMA = "lightwork.google-consent-mode-command.v2"
GPP_HEADER_SCHEMA = "lightwork.gpp-header-artifact.v1"

SUPPORTED_GPP_SPEC_VERSION = 1
SUPPORTED_US_NATIONAL_SECTION_VERSION = 2
SUPPORTED_TCF_ENCODING_VERSION = 2
SUPPORTED_TCF_FRAMEWORK_REVISION = "2.3"

_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_REGION_RE = re.compile(r"[A-Z]{2}(?:-[A-Z0-9]{1,3})?\Z")
_GPP_HEADER_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_WIRE_RE = re.compile(r"[A-Za-z0-9._~-]{1,8192}\Z")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_MAX_PURPOSES = 64
_MAX_VENDORS = 512
_MAX_HEADER_VALUES = 8
_MAX_REGIONS = 64
_MAX_GPP_SECTIONS = 64
_MAX_SECTION_ID = 4095
_MAX_WAIT_MS = 10_000

_ACTIVE_GPP_SECTION_IDS = frozenset({2, 5, *range(7, 28)})
_DEPRECATED_GPP_SECTION_IDS = frozenset({1, 6})
_GOOGLE_REQUIRED_TYPES = frozenset(
    {
        "ad_storage",
        "ad_user_data",
        "ad_personalization",
        "analytics_storage",
    }
)
_GOOGLE_TYPE_ORDER = (
    "ad_storage",
    "ad_user_data",
    "ad_personalization",
    "analytics_storage",
    "functionality_storage",
    "personalization_storage",
    "security_storage",
)
_GOOGLE_TYPES = frozenset(_GOOGLE_TYPE_ORDER)
_CONSENT_STATES = frozenset({"granted", "denied"})
_US_OPT_OUT_STATES = frozenset(
    {"not_applicable", "opted_out", "not_opted_out"}
)
_US_OPT_OUT_CODES = {
    "not_applicable": 0,
    "opted_out": 1,
    "not_opted_out": 2,
}
_BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


class ConsentStandardsError(RuntimeError):
    """Base error for consent-standards interoperability."""


class ConsentStandardsValidationError(ConsentStandardsError, ValueError):
    """Input is outside the closed interoperability contract."""


class UnsupportedConsentStandardError(ConsentStandardsError):
    """A requested standard or wire subset is not implemented here."""


class StaleStandardsBindingError(ConsentStandardsError):
    """A specification, policy, or vendor-list binding is not authoritative."""


class ContradictoryConsentSignalError(ConsentStandardsError):
    """Two explicit inputs cannot both be represented safely."""


class ConsentStandardsIntegrityError(ConsentStandardsError):
    """An encoded artifact is malformed, non-canonical, or rebound."""


def _canonical(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConsentStandardsValidationError(
            "standards data must be finite canonical JSON"
        ) from exc
    if not 1 <= len(encoded) <= 256 * 1024:
        raise ConsentStandardsValidationError("standards data exceeds its byte bound")
    return encoded


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _opaque(value: object, label: str) -> str:
    if not isinstance(value, str) or not _REF_RE.fullmatch(value):
        raise ConsentStandardsValidationError(
            f"{label} must be a bounded opaque identifier"
        )
    if _CONTROL_RE.search(value):
        raise ConsentStandardsValidationError(f"{label} contains control characters")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ConsentStandardsValidationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _positive_int(value: object, label: str, *, maximum: int = 2**31 - 1) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise ConsentStandardsValidationError(
            f"{label} must be an integer between 1 and {maximum}"
        )
    return value


def _strict_tuple(value: object, label: str) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise ConsentStandardsValidationError(f"{label} must be an immutable tuple")
    return value


def _normalized_purposes(value: object) -> tuple[str, ...]:
    raw = _strict_tuple(value, "purpose_ids")
    if not 1 <= len(raw) <= _MAX_PURPOSES:
        raise ConsentStandardsValidationError(
            f"purpose_ids must contain between 1 and {_MAX_PURPOSES} values"
        )
    purposes = tuple(_opaque(item, "purpose_id") for item in raw)
    if len(set(purposes)) != len(purposes):
        raise ConsentStandardsValidationError("purpose_ids contains duplicates")
    return tuple(sorted(purposes))


def _normalized_vendors(
    value: object,
    label: str = "vendor_ids",
    *,
    allow_empty: bool = False,
) -> tuple[int, ...]:
    raw = _strict_tuple(value, label)
    minimum = 0 if allow_empty else 1
    if not minimum <= len(raw) <= _MAX_VENDORS:
        raise ConsentStandardsValidationError(
            f"{label} must contain between {minimum} and {_MAX_VENDORS} values"
        )
    vendors = tuple(
        _positive_int(item, "vendor_id", maximum=65535) for item in raw
    )
    if len(set(vendors)) != len(vendors):
        raise ConsentStandardsValidationError(f"{label} contains duplicates")
    return tuple(sorted(vendors))


@dataclass(frozen=True)
class StandardsVersionContract:
    """Exact external-specification and vendor-list authority binding.

    The caller owns freshness.  This module supports the named revisions, but
    it does not fetch or silently advance a vendor list.  Every transformation
    compares the presented contract with a separately supplied expected
    contract, including all digests and monotonically changing versions.
    """

    contract_version: int
    gpp_spec_version: int
    gpp_spec_sha256: str
    gpp_us_national_section_version: int
    gpp_us_national_spec_sha256: str
    tcf_encoding_version: int
    tcf_framework_revision: str
    tcf_policy_version: int
    tcf_spec_sha256: str
    gvl_specification_version: int
    vendor_list_version: int
    vendor_list_sha256: str

    def normalized(self) -> dict[str, Any]:
        if self.contract_version != SCHEMA_VERSION:
            raise UnsupportedConsentStandardError(
                "only consent-standards contract version 1 is supported"
            )
        if self.gpp_spec_version != SUPPORTED_GPP_SPEC_VERSION:
            raise UnsupportedConsentStandardError("only GPP specification v1 is supported")
        if (
            self.gpp_us_national_section_version
            != SUPPORTED_US_NATIONAL_SECTION_VERSION
        ):
            raise UnsupportedConsentStandardError(
                "only the US National section v2 projection contract is supported"
            )
        if self.tcf_encoding_version != SUPPORTED_TCF_ENCODING_VERSION:
            raise UnsupportedConsentStandardError(
                "only the TCF v2 encoding boundary is recognized"
            )
        if self.tcf_framework_revision != SUPPORTED_TCF_FRAMEWORK_REVISION:
            raise UnsupportedConsentStandardError(
                "only the TCF 2.3 framework boundary is recognized"
            )
        return {
            "schema": "lightwork.consent-standards-version-contract.v1",
            "contract_version": self.contract_version,
            "gpp": {
                "spec_version": self.gpp_spec_version,
                "spec_sha256": _digest(self.gpp_spec_sha256, "gpp_spec_sha256"),
                "us_national_section_version": self.gpp_us_national_section_version,
                "us_national_spec_sha256": _digest(
                    self.gpp_us_national_spec_sha256,
                    "gpp_us_national_spec_sha256",
                ),
            },
            "tcf": {
                "encoding_version": self.tcf_encoding_version,
                "framework_revision": self.tcf_framework_revision,
                "policy_version": _positive_int(
                    self.tcf_policy_version, "tcf_policy_version"
                ),
                "spec_sha256": _digest(self.tcf_spec_sha256, "tcf_spec_sha256"),
                "gvl_specification_version": _positive_int(
                    self.gvl_specification_version,
                    "gvl_specification_version",
                ),
                "vendor_list_version": _positive_int(
                    self.vendor_list_version, "vendor_list_version"
                ),
                "vendor_list_sha256": _digest(
                    self.vendor_list_sha256, "vendor_list_sha256"
                ),
            },
        }

    def digest(self) -> str:
        return _sha256(self.normalized())


def assert_current_contract(
    presented: StandardsVersionContract,
    expected: StandardsVersionContract,
) -> str:
    """Validate exact freshness and return the authoritative contract digest."""

    actual_digest = presented.digest()
    expected_digest = expected.digest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise StaleStandardsBindingError(
            "standards contract is stale or rebound; refresh specifications and "
            "the exact vendor list before transforming consent"
        )
    return expected_digest


@dataclass(frozen=True)
class InteroperabilityBinding:
    """Closed policy/purpose/vendor inputs bound to exact external versions."""

    standards: StandardsVersionContract
    policy_id: str
    policy_version: int
    policy_sha256: str
    notice_sha256: str
    purpose_ids: tuple[str, ...]
    vendor_ids: tuple[int, ...]

    def normalized(
        self,
        expected_contract: StandardsVersionContract,
    ) -> dict[str, Any]:
        contract_digest = assert_current_contract(self.standards, expected_contract)
        return {
            "schema": BINDING_SCHEMA,
            "contract_sha256": contract_digest,
            "policy_id": _opaque(self.policy_id, "policy_id"),
            "policy_version": _positive_int(self.policy_version, "policy_version"),
            "policy_sha256": _digest(self.policy_sha256, "policy_sha256"),
            "notice_sha256": _digest(self.notice_sha256, "notice_sha256"),
            "purpose_ids": list(_normalized_purposes(self.purpose_ids)),
            "vendor_ids": list(_normalized_vendors(self.vendor_ids)),
        }

    def digest(self, expected_contract: StandardsVersionContract) -> str:
        return _sha256(self.normalized(expected_contract))


@dataclass(frozen=True)
class BrowserPrivacySignals:
    """Sanitized semantics of recognized browser privacy request headers."""

    gpc: bool
    dnt: str
    precedence: str
    conflict: bool
    source_sha256: str

    def as_dict(self) -> dict[str, Any]:
        if self.dnt not in {"unset", "do_not_track", "tracking_allowed"}:
            raise ConsentStandardsValidationError("dnt state is invalid")
        if self.precedence not in {"gpc", "dnt", "none"}:
            raise ConsentStandardsValidationError("signal precedence is invalid")
        if not isinstance(self.gpc, bool) or not isinstance(self.conflict, bool):
            raise ConsentStandardsValidationError("signal booleans are invalid")
        return {
            "schema": SIGNAL_SCHEMA,
            "gpc": self.gpc,
            "dnt": self.dnt,
            "precedence": self.precedence,
            "conflict": self.conflict,
            "source_sha256": _digest(self.source_sha256, "source_sha256"),
        }


def _header_values(value: object, label: str) -> tuple[str, ...]:
    if isinstance(value, str):
        values: tuple[object, ...] = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = tuple(value)
    else:
        raise ConsentStandardsValidationError(
            f"{label} header must be a string or bounded sequence of strings"
        )
    if not 1 <= len(values) <= _MAX_HEADER_VALUES:
        raise ConsentStandardsValidationError(f"{label} has too many field values")
    normalized: list[str] = []
    for item in values:
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= 16
            or _CONTROL_RE.search(item)
        ):
            raise ConsentStandardsValidationError(f"{label} field value is invalid")
        normalized.append(item)
    return tuple(normalized)


def detect_privacy_headers(headers: Mapping[str, object]) -> BrowserPrivacySignals:
    """Detect ``Sec-GPC`` and ``DNT`` with an explicit privacy-floor precedence.

    Per the GPC specification, at least one exact ``Sec-GPC: 1`` wins when
    duplicate field values are presented; otherwise invalid GPC values are
    rejected by this strict boundary.  Only exact legacy ``DNT: 0`` and
    ``DNT: 1`` values are supported.  GPC takes precedence over DNT for the
    do-not-sell/share and cross-context-advertising privacy floor, while the DNT
    observation is retained rather than being reinterpreted as GPC.
    """

    if not isinstance(headers, Mapping):
        raise ConsentStandardsValidationError("headers must be a mapping")
    gpc_values: list[str] = []
    dnt_values: list[str] = []
    for name, raw_value in headers.items():
        if not isinstance(name, str) or _CONTROL_RE.search(name):
            raise ConsentStandardsValidationError("header names must be strings")
        lowered = name.casefold()
        if lowered == "sec-gpc":
            gpc_values.extend(_header_values(raw_value, "Sec-GPC"))
        elif lowered == "dnt":
            dnt_values.extend(_header_values(raw_value, "DNT"))
    if len(gpc_values) > _MAX_HEADER_VALUES or len(dnt_values) > _MAX_HEADER_VALUES:
        raise ConsentStandardsValidationError("too many duplicate privacy headers")

    if "1" in gpc_values:
        gpc = True
    elif gpc_values:
        raise ConsentStandardsValidationError(
            "Sec-GPC is supported only when its field value is exactly '1'"
        )
    else:
        gpc = False

    unknown_dnt = set(dnt_values) - {"0", "1"}
    if unknown_dnt:
        raise UnsupportedConsentStandardError(
            "DNT extensions and unknown field values are not supported"
        )
    if {"0", "1"}.issubset(set(dnt_values)):
        raise ContradictoryConsentSignalError(
            "the request contains contradictory DNT field values"
        )
    if "1" in dnt_values:
        dnt = "do_not_track"
    elif "0" in dnt_values:
        dnt = "tracking_allowed"
    else:
        dnt = "unset"

    precedence = "gpc" if gpc else ("dnt" if dnt == "do_not_track" else "none")
    conflict = gpc and dnt == "tracking_allowed"
    sanitized = {
        "schema": SIGNAL_SCHEMA,
        "gpc": gpc,
        "dnt": dnt,
        "precedence": precedence,
        "conflict": conflict,
    }
    return BrowserPrivacySignals(
        gpc=gpc,
        dnt=dnt,
        precedence=precedence,
        conflict=conflict,
        source_sha256=_sha256(sanitized),
    )


@dataclass(frozen=True)
class USPrivacyPurposeMap:
    """Internal purposes and vendors to which the US projection applies."""

    sale_purpose_id: str
    sharing_purpose_id: str
    targeted_advertising_purpose_id: str
    vendor_ids: tuple[int, ...]

    def normalized(self, binding: dict[str, Any]) -> dict[str, Any]:
        purposes = (
            _opaque(self.sale_purpose_id, "sale_purpose_id"),
            _opaque(self.sharing_purpose_id, "sharing_purpose_id"),
            _opaque(
                self.targeted_advertising_purpose_id,
                "targeted_advertising_purpose_id",
            ),
        )
        if len(set(purposes)) != len(purposes):
            raise ConsentStandardsValidationError(
                "US privacy purpose bindings must be distinct"
            )
        binding_purposes = set(binding["purpose_ids"])
        if not set(purposes).issubset(binding_purposes):
            raise ConsentStandardsValidationError(
                "US privacy purpose is absent from the policy binding"
            )
        vendors = _normalized_vendors(self.vendor_ids)
        if not set(vendors).issubset(set(binding["vendor_ids"])):
            raise ConsentStandardsValidationError(
                "US privacy vendor is absent from the vendor-list binding"
            )
        return {
            "sale": purposes[0],
            "sharing": purposes[1],
            "targeted_advertising": purposes[2],
            "vendor_ids": list(vendors),
        }


@dataclass(frozen=True)
class USPrivacyChoices:
    """Bounded choices for the accurately projected US opt-out subset."""

    sale: str
    sharing: str
    targeted_advertising: str

    def normalized(self) -> dict[str, str]:
        values = {
            "sale": self.sale,
            "sharing": self.sharing,
            "targeted_advertising": self.targeted_advertising,
        }
        if any(value not in _US_OPT_OUT_STATES for value in values.values()):
            raise ConsentStandardsValidationError("US opt-out state is invalid")
        return values


@dataclass(frozen=True)
class USPrivacyProjection:
    """Non-wire internal projection of GPP-relevant US opt-out fields."""

    fields: tuple[tuple[str, int], ...]
    gpc: bool
    dnt: str
    binding_sha256: str
    purpose_binding_sha256: str
    artifact_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": US_PROJECTION_SCHEMA,
            "scope": "internal-projection-only",
            "wire_conformant": False,
            "gpp_section": {"id": 7, "version": 2},
            "fields": dict(self.fields),
            "gpc_subsection": {"Gpc": self.gpc},
            "legacy_dnt": self.dnt,
            "binding_sha256": _digest(
                self.binding_sha256, "binding_sha256"
            ),
            "purpose_binding_sha256": _digest(
                self.purpose_binding_sha256, "purpose_binding_sha256"
            ),
            "artifact_sha256": _digest(
                self.artifact_sha256, "artifact_sha256"
            ),
        }


def project_us_privacy_signals(
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
    purpose_map: USPrivacyPurposeMap,
    choices: USPrivacyChoices,
    signals: BrowserPrivacySignals,
) -> USPrivacyProjection:
    """Project the supported US opt-out subset without claiming GPP encoding."""

    binding_value = binding.normalized(expected_contract)
    binding_digest = _sha256(binding_value)
    purpose_value = purpose_map.normalized(binding_value)
    purpose_digest = _sha256(purpose_value)
    choice_value = choices.normalized()
    signal_value = signals.as_dict()

    if signals.gpc:
        conflicts = [
            name
            for name, state in choice_value.items()
            if state == "not_opted_out"
        ]
        if conflicts:
            raise ContradictoryConsentSignalError(
                "GPC cannot be combined with an explicit not-opted-out choice "
                f"for {','.join(sorted(conflicts))}"
            )
        choice_value = {
            name: ("opted_out" if state != "not_applicable" else state)
            for name, state in choice_value.items()
        }
    if (
        signals.dnt == "do_not_track"
        and choice_value["targeted_advertising"] == "not_opted_out"
    ):
        raise ContradictoryConsentSignalError(
            "DNT:1 cannot be combined with an explicit targeted-advertising grant"
        )

    fields = (
        ("SaleOptOut", _US_OPT_OUT_CODES[choice_value["sale"]]),
        ("SharingOptOut", _US_OPT_OUT_CODES[choice_value["sharing"]]),
        (
            "TargetedAdvertisingOptOut",
            _US_OPT_OUT_CODES[choice_value["targeted_advertising"]],
        ),
    )
    artifact_payload = {
        "schema": US_PROJECTION_SCHEMA,
        "scope": "internal-projection-only",
        "wire_conformant": False,
        "gpp_section": {"id": 7, "version": 2},
        "fields": dict(fields),
        "gpc_subsection": {"Gpc": signals.gpc},
        "legacy_dnt": signals.dnt,
        "signal_source_sha256": signal_value["source_sha256"],
        "binding_sha256": binding_digest,
        "purpose_binding_sha256": purpose_digest,
    }
    return USPrivacyProjection(
        fields=fields,
        gpc=signals.gpc,
        dnt=signals.dnt,
        binding_sha256=binding_digest,
        purpose_binding_sha256=purpose_digest,
        artifact_sha256=_sha256(artifact_payload),
    )


@dataclass(frozen=True)
class PurposeDecision:
    """One explicit purpose decision for an exact vendor set."""

    purpose_id: str
    vendor_ids: tuple[int, ...]
    state: str

    def normalized(self, binding: dict[str, Any]) -> dict[str, Any]:
        purpose = _opaque(self.purpose_id, "purpose_id")
        if purpose not in set(binding["purpose_ids"]):
            raise ConsentStandardsValidationError(
                "decision purpose is absent from the policy binding"
            )
        vendors = _normalized_vendors(self.vendor_ids)
        if not set(vendors).issubset(set(binding["vendor_ids"])):
            raise ConsentStandardsValidationError(
                "decision vendor is absent from the vendor-list binding"
            )
        if self.state not in _CONSENT_STATES:
            raise ConsentStandardsValidationError("decision state must be granted or denied")
        return {
            "purpose_id": purpose,
            "vendor_ids": list(vendors),
            "state": self.state,
        }


@dataclass(frozen=True)
class GoogleConsentPurpose:
    """Map one Google Consent Mode type to one internal purpose/vendor set."""

    consent_type: str
    purpose_id: str
    vendor_ids: tuple[int, ...]

    def normalized(self, binding: dict[str, Any]) -> dict[str, Any]:
        if self.consent_type not in _GOOGLE_TYPES:
            raise ConsentStandardsValidationError("Google consent type is unsupported")
        purpose = _opaque(self.purpose_id, "purpose_id")
        if purpose not in set(binding["purpose_ids"]):
            raise ConsentStandardsValidationError(
                "Google purpose is absent from the policy binding"
            )
        vendors = _normalized_vendors(self.vendor_ids)
        if not set(vendors).issubset(set(binding["vendor_ids"])):
            raise ConsentStandardsValidationError(
                "Google vendor is absent from the vendor-list binding"
            )
        return {
            "consent_type": self.consent_type,
            "purpose_id": purpose,
            "vendor_ids": list(vendors),
        }


@dataclass(frozen=True)
class GoogleConsentCommand:
    """Immutable Google Consent Mode v2 command with binding evidence."""

    phase: str
    consent_values: tuple[tuple[str, str], ...]
    wait_for_update_ms: int | None
    regions: tuple[str, ...]
    binding_sha256: str
    mapping_sha256: str
    decision_sha256: str
    signal_source_sha256: str
    artifact_sha256: str

    def parameters(self) -> dict[str, object]:
        result: dict[str, object] = dict(self.consent_values)
        if self.wait_for_update_ms is not None:
            result["wait_for_update"] = self.wait_for_update_ms
        if self.regions:
            result["region"] = list(self.regions)
        return result

    def gtag_arguments(self) -> tuple[str, str, dict[str, object]]:
        return ("consent", self.phase, self.parameters())

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": GOOGLE_COMMAND_SCHEMA,
            "consent_mode_version": 2,
            "phase": self.phase,
            "parameters": self.parameters(),
            "binding_sha256": _digest(
                self.binding_sha256, "binding_sha256"
            ),
            "mapping_sha256": _digest(
                self.mapping_sha256, "mapping_sha256"
            ),
            "decision_sha256": _digest(
                self.decision_sha256, "decision_sha256"
            ),
            "signal_source_sha256": _digest(
                self.signal_source_sha256, "signal_source_sha256"
            ),
            "artifact_sha256": _digest(
                self.artifact_sha256, "artifact_sha256"
            ),
        }


def _regions(value: object) -> tuple[str, ...]:
    raw = _strict_tuple(value, "regions")
    if len(raw) > _MAX_REGIONS:
        raise ConsentStandardsValidationError("regions contains too many values")
    regions: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not _REGION_RE.fullmatch(item):
            raise ConsentStandardsValidationError(
                "region must be a bounded uppercase ISO-style region code"
            )
        regions.append(item)
    if len(set(regions)) != len(regions):
        raise ConsentStandardsValidationError("regions contains duplicates")
    return tuple(sorted(regions))


def google_consent_mode_v2(
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
    *,
    phase: str,
    mappings: tuple[GoogleConsentPurpose, ...],
    decisions: tuple[PurposeDecision, ...],
    signals: BrowserPrivacySignals,
    wait_for_update_ms: int | None = None,
    regions: tuple[str, ...] = (),
) -> GoogleConsentCommand:
    """Create a strict Google Consent Mode v2 ``default`` or ``update`` command."""

    if phase not in {"default", "update"}:
        raise ConsentStandardsValidationError("phase must be default or update")
    binding_value = binding.normalized(expected_contract)
    binding_digest = _sha256(binding_value)
    raw_mappings = _strict_tuple(mappings, "mappings")
    raw_decisions = _strict_tuple(decisions, "decisions")
    if not 4 <= len(raw_mappings) <= len(_GOOGLE_TYPES):
        raise ConsentStandardsValidationError(
            "Google mappings must cover the four required v2 types"
        )
    if not 1 <= len(raw_decisions) <= _MAX_PURPOSES:
        raise ConsentStandardsValidationError("decisions has an invalid size")

    mapping_values = [item.normalized(binding_value) for item in raw_mappings]
    consent_types = [item["consent_type"] for item in mapping_values]
    if len(set(consent_types)) != len(consent_types):
        raise ConsentStandardsValidationError("Google consent types must be unique")
    if not _GOOGLE_REQUIRED_TYPES.issubset(consent_types):
        raise ConsentStandardsValidationError(
            "Google Consent Mode v2 requires ad_storage, ad_user_data, "
            "ad_personalization, and analytics_storage"
        )
    purpose_ids = [item["purpose_id"] for item in mapping_values]
    if len(set(purpose_ids)) != len(purpose_ids):
        raise ConsentStandardsValidationError(
            "each Google consent type must bind to a distinct internal purpose"
        )

    decision_values = [item.normalized(binding_value) for item in raw_decisions]
    decisions_by_key: dict[tuple[str, tuple[int, ...]], str] = {}
    for item in decision_values:
        key = (item["purpose_id"], tuple(item["vendor_ids"]))
        if key in decisions_by_key:
            if decisions_by_key[key] != item["state"]:
                raise ContradictoryConsentSignalError(
                    "the same purpose/vendor set has contradictory decisions"
                )
            raise ConsentStandardsValidationError(
                "the same purpose/vendor decision was supplied more than once"
            )
        decisions_by_key[key] = item["state"]

    consent_values: list[tuple[str, str]] = []
    for consent_type in _GOOGLE_TYPE_ORDER:
        candidates = [
            item for item in mapping_values if item["consent_type"] == consent_type
        ]
        if not candidates:
            continue
        item = candidates[0]
        key = (item["purpose_id"], tuple(item["vendor_ids"]))
        if key not in decisions_by_key:
            raise ConsentStandardsValidationError(
                f"missing exact purpose/vendor decision for {consent_type}"
            )
        consent_values.append((consent_type, decisions_by_key[key]))

    unused_decisions = set(decisions_by_key) - {
        (item["purpose_id"], tuple(item["vendor_ids"])) for item in mapping_values
    }
    if unused_decisions:
        raise ConsentStandardsValidationError(
            "decisions contains an unconsumed purpose/vendor input"
        )

    values = dict(consent_values)
    if signals.gpc and any(
        values.get(consent_type) == "granted"
        for consent_type in ("ad_user_data", "ad_personalization")
    ):
        raise ContradictoryConsentSignalError(
            "GPC conflicts with granted ad-user-data or ad-personalization consent"
        )
    if signals.dnt == "do_not_track" and values.get("ad_personalization") == "granted":
        raise ContradictoryConsentSignalError(
            "DNT:1 conflicts with granted ad-personalization consent"
        )

    normalized_regions = _regions(regions)
    if phase == "update" and (wait_for_update_ms is not None or normalized_regions):
        raise ConsentStandardsValidationError(
            "wait_for_update and region are supported only on default commands"
        )
    if wait_for_update_ms is not None:
        wait_for_update_ms = _positive_int(
            wait_for_update_ms,
            "wait_for_update_ms",
            maximum=_MAX_WAIT_MS,
        )

    mapping_value = sorted(mapping_values, key=lambda item: item["consent_type"])
    decision_value = sorted(
        decision_values,
        key=lambda item: (item["purpose_id"], item["vendor_ids"]),
    )
    parameters: dict[str, object] = dict(consent_values)
    if wait_for_update_ms is not None:
        parameters["wait_for_update"] = wait_for_update_ms
    if normalized_regions:
        parameters["region"] = list(normalized_regions)
    mapping_digest = _sha256(mapping_value)
    decision_digest = _sha256(decision_value)
    signal_value = signals.as_dict()
    artifact_payload = {
        "schema": GOOGLE_COMMAND_SCHEMA,
        "consent_mode_version": 2,
        "phase": phase,
        "parameters": parameters,
        "binding_sha256": binding_digest,
        "mapping_sha256": mapping_digest,
        "decision_sha256": decision_digest,
        "signal_source_sha256": signal_value["source_sha256"],
    }
    return GoogleConsentCommand(
        phase=phase,
        consent_values=tuple(consent_values),
        wait_for_update_ms=wait_for_update_ms,
        regions=normalized_regions,
        binding_sha256=binding_digest,
        mapping_sha256=mapping_digest,
        decision_sha256=decision_digest,
        signal_source_sha256=signal_value["source_sha256"],
        artifact_sha256=_sha256(artifact_payload),
    )


def _fibonacci_encode(value: int) -> str:
    value = _positive_int(value, "Fibonacci value", maximum=_MAX_SECTION_ID)
    fibonacci = [1, 2]
    while fibonacci[-1] < value:
        fibonacci.append(fibonacci[-1] + fibonacci[-2])
    if fibonacci[-1] > value:
        fibonacci.pop()
    bits = ["0"] * len(fibonacci)
    remainder = value
    for index in range(len(fibonacci) - 1, -1, -1):
        number = fibonacci[index]
        if number <= remainder:
            bits[index] = "1"
            remainder -= number
    if remainder != 0:
        raise ConsentStandardsIntegrityError("Fibonacci encoding failed")
    return "".join(bits) + "1"


def _fibonacci_decode(bits: str, position: int) -> tuple[int, int]:
    fibonacci = [1, 2]
    value = 0
    previous_one = False
    data_index = 0
    while position < len(bits):
        bit = bits[position]
        position += 1
        if bit == "1" and previous_one:
            if value < 1 or value > _MAX_SECTION_ID:
                raise ConsentStandardsIntegrityError(
                    "GPP Fibonacci value is outside the supported bound"
                )
            return value, position
        if data_index >= len(fibonacci):
            fibonacci.append(fibonacci[-1] + fibonacci[-2])
        if bit == "1":
            value += fibonacci[data_index]
        previous_one = bit == "1"
        data_index += 1
    raise ConsentStandardsIntegrityError("GPP Fibonacci value is truncated")


def _section_ranges(section_ids: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start = section_ids[0]
    end = start
    for section_id in section_ids[1:]:
        if section_id == end + 1:
            end = section_id
        else:
            ranges.append((start, end))
            start = section_id
            end = section_id
    ranges.append((start, end))
    return tuple(ranges)


def _normalize_section_ids(
    value: object,
    *,
    allow_deprecated: bool,
) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ConsentStandardsValidationError("section_ids must be a bounded sequence")
    raw = tuple(value)
    if not 1 <= len(raw) <= _MAX_GPP_SECTIONS:
        raise ConsentStandardsValidationError(
            f"section_ids must contain between 1 and {_MAX_GPP_SECTIONS} values"
        )
    sections = tuple(
        _positive_int(item, "section_id", maximum=_MAX_SECTION_ID) for item in raw
    )
    if len(set(sections)) != len(sections):
        raise ConsentStandardsValidationError("section_ids contains duplicates")
    sections = tuple(sorted(sections))
    unsupported = set(sections) - _ACTIVE_GPP_SECTION_IDS - _DEPRECATED_GPP_SECTION_IDS
    if unsupported:
        raise UnsupportedConsentStandardError(
            "GPP header contains an unknown or unsupported section ID"
        )
    if not allow_deprecated and set(sections) & _DEPRECATED_GPP_SECTION_IDS:
        raise UnsupportedConsentStandardError(
            "deprecated GPP sections require explicit allow_deprecated=True"
        )
    return sections


def _encode_gpp_header_value(section_ids: tuple[int, ...]) -> str:
    bits = f"{3:06b}{SUPPORTED_GPP_SPEC_VERSION:06b}"
    ranges = _section_ranges(section_ids)
    bits += f"{len(ranges):012b}"
    last_seen = 0
    for start, end in ranges:
        is_range = start != end
        bits += "1" if is_range else "0"
        bits += _fibonacci_encode(start - last_seen)
        if is_range:
            bits += _fibonacci_encode(end - start)
        last_seen = end
    bits += "0" * ((-len(bits)) % 6)
    return "".join(
        _BASE64URL[int(bits[index : index + 6], 2)]
        for index in range(0, len(bits), 6)
    )


def _decode_gpp_header_value(encoded: str) -> tuple[int, tuple[int, ...]]:
    if not isinstance(encoded, str) or not _GPP_HEADER_RE.fullmatch(encoded):
        raise ConsentStandardsValidationError(
            "GPP header must be a bounded base64url-like string"
        )
    try:
        bits = "".join(f"{_BASE64URL.index(character):06b}" for character in encoded)
    except ValueError as exc:  # guarded by the regex; retained for defensive use
        raise ConsentStandardsIntegrityError("GPP header alphabet is invalid") from exc
    if len(bits) < 24:
        raise ConsentStandardsIntegrityError("GPP header is truncated")
    field_type = int(bits[:6], 2)
    version = int(bits[6:12], 2)
    amount = int(bits[12:24], 2)
    if field_type != 3:
        raise ConsentStandardsIntegrityError("encoded value is not a GPP header")
    if version != SUPPORTED_GPP_SPEC_VERSION:
        raise UnsupportedConsentStandardError("GPP header version is unsupported")
    if not 1 <= amount <= _MAX_GPP_SECTIONS:
        raise ConsentStandardsIntegrityError("GPP header range count is invalid")
    position = 24
    last_seen = 0
    sections: list[int] = []
    for _ in range(amount):
        if position >= len(bits):
            raise ConsentStandardsIntegrityError("GPP header range is truncated")
        is_range = bits[position] == "1"
        position += 1
        offset, position = _fibonacci_decode(bits, position)
        start = last_seen + offset
        end = start
        if is_range:
            length, position = _fibonacci_decode(bits, position)
            end = start + length
        if end > _MAX_SECTION_ID or start <= last_seen:
            raise ConsentStandardsIntegrityError("GPP section range is invalid")
        sections.extend(range(start, end + 1))
        if len(sections) > _MAX_GPP_SECTIONS:
            raise ConsentStandardsIntegrityError("GPP header contains too many sections")
        last_seen = end
    if "1" in bits[position:]:
        raise ConsentStandardsIntegrityError("GPP header has non-zero trailing bits")
    return version, tuple(sections)


@dataclass(frozen=True)
class GPPHeaderArtifact:
    """Official GPP v1 header bytes plus Lightwork binding evidence."""

    encoded: str
    section_ids: tuple[int, ...]
    spec_version: int
    binding_sha256: str
    payload_sha256: str
    artifact_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": GPP_HEADER_SCHEMA,
            "scope": "gpp-header-only",
            "encoded": self.encoded,
            "section_ids": list(self.section_ids),
            "spec_version": self.spec_version,
            "binding_sha256": _digest(
                self.binding_sha256, "binding_sha256"
            ),
            "payload_sha256": _digest(
                self.payload_sha256, "payload_sha256"
            ),
            "artifact_sha256": _digest(
                self.artifact_sha256, "artifact_sha256"
            ),
        }


def _gpp_artifact(
    encoded: str,
    sections: tuple[int, ...],
    binding_digest: str,
) -> GPPHeaderArtifact:
    payload = {
        "encoded": encoded,
        "section_ids": list(sections),
        "spec_version": SUPPORTED_GPP_SPEC_VERSION,
    }
    payload_digest = _sha256(payload)
    artifact_payload = {
        "schema": GPP_HEADER_SCHEMA,
        "scope": "gpp-header-only",
        **payload,
        "binding_sha256": binding_digest,
        "payload_sha256": payload_digest,
    }
    return GPPHeaderArtifact(
        encoded=encoded,
        section_ids=sections,
        spec_version=SUPPORTED_GPP_SPEC_VERSION,
        binding_sha256=binding_digest,
        payload_sha256=payload_digest,
        artifact_sha256=_sha256(artifact_payload),
    )


def encode_gpp_header(
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
    section_ids: Sequence[int],
    *,
    allow_deprecated: bool = False,
) -> GPPHeaderArtifact:
    """Encode exactly the GPP v1 header subset and bind it to policy evidence."""

    binding_digest = binding.digest(expected_contract)
    sections = _normalize_section_ids(
        section_ids,
        allow_deprecated=allow_deprecated,
    )
    encoded = _encode_gpp_header_value(sections)
    return _gpp_artifact(encoded, sections, binding_digest)


def decode_gpp_header(
    encoded: str,
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
    *,
    allow_deprecated: bool = False,
) -> GPPHeaderArtifact:
    """Decode a canonical GPP v1 header; discrete sections remain unsupported."""

    binding_digest = binding.digest(expected_contract)
    version, decoded = _decode_gpp_header_value(encoded)
    sections = _normalize_section_ids(
        decoded,
        allow_deprecated=allow_deprecated,
    )
    canonical = _encode_gpp_header_value(sections)
    if not hmac.compare_digest(canonical, encoded):
        raise ConsentStandardsIntegrityError("GPP header is not canonically encoded")
    if version != SUPPORTED_GPP_SPEC_VERSION:
        raise UnsupportedConsentStandardError("GPP header version is unsupported")
    return _gpp_artifact(encoded, sections, binding_digest)


def _bounded_wire(value: object, label: str) -> str:
    if not isinstance(value, str) or not _WIRE_RE.fullmatch(value):
        raise ConsentStandardsValidationError(
            f"{label} must be a bounded standard-wire token"
        )
    return value


def encode_tcf_v2_3(
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
) -> NoReturn:
    """Fail closed: TCF 2.3 wire encoding is not implemented or certified."""

    binding.normalized(expected_contract)
    raise UnsupportedConsentStandardError(
        "TCF 2.3 wire encoding is not implemented; use a verified TCF codec and "
        "preserve this binding at the integration boundary"
    )


def decode_tcf_v2_3(
    encoded: str,
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
) -> NoReturn:
    """Fail closed: TCF 2.3 wire decoding is not implemented or certified."""

    binding.normalized(expected_contract)
    _bounded_wire(encoded, "TCF string")
    raise UnsupportedConsentStandardError(
        "TCF 2.3 wire decoding is not implemented; no TC String was interpreted"
    )


def encode_gpp_us_national_v2(
    projection: USPrivacyProjection,
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
) -> NoReturn:
    """Fail closed: the internal projection is not a US-National wire string."""

    binding_digest = binding.digest(expected_contract)
    projection_value = projection.as_dict()
    if not hmac.compare_digest(
        projection_value["binding_sha256"],
        binding_digest,
    ):
        raise ConsentStandardsIntegrityError(
            "US projection belongs to a different policy binding"
        )
    raise UnsupportedConsentStandardError(
        "GPP US-National v2 discrete-section encoding is not implemented; the "
        "projection cannot be promoted to a wire-format claim"
    )


def decode_gpp_discrete_section(
    section_id: int,
    encoded: str,
    binding: InteroperabilityBinding,
    expected_contract: StandardsVersionContract,
) -> NoReturn:
    """Fail closed for every GPP discrete-section decoder."""

    binding.normalized(expected_contract)
    _positive_int(section_id, "section_id", maximum=_MAX_SECTION_ID)
    _bounded_wire(encoded, "GPP discrete section")
    raise UnsupportedConsentStandardError(
        "GPP discrete-section decoding is not implemented; only the v1 header "
        "codec is supported"
    )
