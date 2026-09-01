from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from maverick.consent_standards import (
    BrowserPrivacySignals,
    ConsentStandardsIntegrityError,
    ConsentStandardsValidationError,
    ContradictoryConsentSignalError,
    GoogleConsentPurpose,
    InteroperabilityBinding,
    PurposeDecision,
    StaleStandardsBindingError,
    StandardsVersionContract,
    UnsupportedConsentStandardError,
    USPrivacyChoices,
    USPrivacyPurposeMap,
    assert_current_contract,
    decode_gpp_discrete_section,
    decode_gpp_header,
    decode_tcf_v2_3,
    detect_privacy_headers,
    encode_gpp_header,
    encode_gpp_us_national_v2,
    encode_tcf_v2_3,
    google_consent_mode_v2,
    project_us_privacy_signals,
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _contract(**overrides: object) -> StandardsVersionContract:
    values: dict[str, object] = {
        "contract_version": 1,
        "gpp_spec_version": 1,
        "gpp_spec_sha256": _digest("gpp-spec-v1"),
        "gpp_us_national_section_version": 2,
        "gpp_us_national_spec_sha256": _digest("usnat-spec-v2"),
        "tcf_encoding_version": 2,
        "tcf_framework_revision": "2.3",
        "tcf_policy_version": 5,
        "tcf_spec_sha256": _digest("tcf-spec-2.3"),
        "gvl_specification_version": 3,
        "vendor_list_version": 91,
        "vendor_list_sha256": _digest("gvl-91"),
    }
    values.update(overrides)
    return StandardsVersionContract(**values)  # type: ignore[arg-type]


def _binding(
    contract: StandardsVersionContract | None = None,
    **overrides: object,
) -> InteroperabilityBinding:
    values: dict[str, object] = {
        "standards": contract or _contract(),
        "policy_id": "policy.us-and-ads",
        "policy_version": 7,
        "policy_sha256": _digest("policy-v7"),
        "notice_sha256": _digest("notice-v7"),
        "purpose_ids": (
            "us.sale",
            "us.sharing",
            "us.targeted_ads",
            "google.ad_storage",
            "google.ad_user_data",
            "google.ad_personalization",
            "google.analytics_storage",
        ),
        "vendor_ids": (755, 1001),
    }
    values.update(overrides)
    return InteroperabilityBinding(**values)  # type: ignore[arg-type]


def _us_map(**overrides: object) -> USPrivacyPurposeMap:
    values: dict[str, object] = {
        "sale_purpose_id": "us.sale",
        "sharing_purpose_id": "us.sharing",
        "targeted_advertising_purpose_id": "us.targeted_ads",
        "vendor_ids": (755, 1001),
    }
    values.update(overrides)
    return USPrivacyPurposeMap(**values)  # type: ignore[arg-type]


def _google_mappings() -> tuple[GoogleConsentPurpose, ...]:
    return (
        GoogleConsentPurpose("ad_storage", "google.ad_storage", (755,)),
        GoogleConsentPurpose("ad_user_data", "google.ad_user_data", (755,)),
        GoogleConsentPurpose(
            "ad_personalization",
            "google.ad_personalization",
            (755,),
        ),
        GoogleConsentPurpose(
            "analytics_storage",
            "google.analytics_storage",
            (755,),
        ),
    )


def _google_decisions(
    *,
    ad_storage: str = "denied",
    ad_user_data: str = "denied",
    ad_personalization: str = "denied",
    analytics_storage: str = "granted",
) -> tuple[PurposeDecision, ...]:
    return (
        PurposeDecision("google.ad_storage", (755,), ad_storage),
        PurposeDecision("google.ad_user_data", (755,), ad_user_data),
        PurposeDecision(
            "google.ad_personalization",
            (755,),
            ad_personalization,
        ),
        PurposeDecision(
            "google.analytics_storage",
            (755,),
            analytics_storage,
        ),
    )


def _no_signals() -> BrowserPrivacySignals:
    return detect_privacy_headers({})


def test_version_contract_and_binding_are_deterministic() -> None:
    contract = _contract()
    binding = _binding(
        contract,
        purpose_ids=tuple(reversed(_binding().purpose_ids)),
        vendor_ids=(1001, 755),
    )
    canonical = _binding(contract)

    assert contract.digest() == _contract().digest()
    assert binding.digest(contract) == canonical.digest(contract)
    normalized = binding.normalized(contract)
    assert normalized["purpose_ids"] == sorted(normalized["purpose_ids"])
    assert normalized["vendor_ids"] == [755, 1001]
    assert normalized["policy_sha256"] == _digest("policy-v7")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vendor_list_version", 92),
        ("vendor_list_sha256", _digest("gvl-92")),
        ("gpp_spec_sha256", _digest("different-gpp")),
        ("tcf_policy_version", 6),
        ("tcf_spec_sha256", _digest("different-tcf")),
    ],
)
def test_stale_contract_rejects_every_digest_and_version_change(
    field: str,
    value: object,
) -> None:
    expected = _contract()
    presented = replace(expected, **{field: value})

    with pytest.raises(StaleStandardsBindingError):
        assert_current_contract(presented, expected)
    with pytest.raises(StaleStandardsBindingError):
        _binding(presented).digest(expected)


@pytest.mark.parametrize(
    "contract",
    [
        _contract(contract_version=2),
        _contract(gpp_spec_version=2),
        _contract(gpp_us_national_section_version=1),
        _contract(tcf_encoding_version=3),
        _contract(tcf_framework_revision="2.4"),
    ],
)
def test_unknown_standard_versions_fail_closed(
    contract: StandardsVersionContract,
) -> None:
    with pytest.raises(UnsupportedConsentStandardError):
        contract.normalized()


@pytest.mark.parametrize(
    "binding",
    [
        _binding(policy_id="raw policy words"),
        _binding(policy_sha256="not-a-digest"),
        _binding(purpose_ids=("purpose one",)),
        _binding(purpose_ids=("duplicate", "duplicate")),
        _binding(purpose_ids=tuple(f"purpose-{index}" for index in range(65))),
        _binding(vendor_ids=(755, 755)),
        _binding(vendor_ids=()),
        _binding(vendor_ids=(0,)),
    ],
)
def test_binding_rejects_free_text_duplicates_and_overlong_lists(
    binding: InteroperabilityBinding,
) -> None:
    with pytest.raises(ConsentStandardsValidationError):
        binding.normalized(_contract())


def test_binding_rejects_mutable_collections() -> None:
    binding = _binding(purpose_ids=["us.sale"])  # type: ignore[arg-type]
    with pytest.raises(ConsentStandardsValidationError, match="immutable tuple"):
        binding.normalized(_contract())


def test_sec_gpc_exact_value_wins_and_dnt_is_retained() -> None:
    signals = detect_privacy_headers(
        {
            "sec-gpc": ("invalid", "1"),
            "DNT": "0",
            "User-Agent": "not retained",
        }
    )

    assert signals.gpc is True
    assert signals.dnt == "tracking_allowed"
    assert signals.precedence == "gpc"
    assert signals.conflict is True
    assert signals.as_dict()["source_sha256"] == signals.source_sha256
    assert "User-Agent" not in repr(signals)


def test_dnt_detection_and_unset_semantics() -> None:
    assert detect_privacy_headers({"DNT": "1"}).as_dict() == {
        "schema": "lightwork.browser-privacy-signals.v1",
        "gpc": False,
        "dnt": "do_not_track",
        "precedence": "dnt",
        "conflict": False,
        "source_sha256": detect_privacy_headers({"DNT": "1"}).source_sha256,
    }
    assert detect_privacy_headers({"DNT": "0"}).precedence == "none"
    assert detect_privacy_headers({}).dnt == "unset"


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({"Sec-GPC": "0"}, ConsentStandardsValidationError),
        ({"Sec-GPC": "true"}, ConsentStandardsValidationError),
        ({"DNT": "1-extension"}, UnsupportedConsentStandardError),
        ({"DNT": ("0", "1")}, ContradictoryConsentSignalError),
        ({"DNT": " 1"}, UnsupportedConsentStandardError),
        ({"Sec-GPC": ["1"] * 9}, ConsentStandardsValidationError),
    ],
)
def test_unknown_or_contradictory_header_values_fail_closed(
    headers: dict[str, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        detect_privacy_headers(headers)


def test_us_projection_honors_gpc_and_preserves_not_applicable() -> None:
    contract = _contract()
    projection = project_us_privacy_signals(
        _binding(contract),
        contract,
        _us_map(),
        USPrivacyChoices(
            sale="not_applicable",
            sharing="opted_out",
            targeted_advertising="opted_out",
        ),
        detect_privacy_headers({"Sec-GPC": "1"}),
    )
    value = projection.as_dict()

    assert value["scope"] == "internal-projection-only"
    assert value["wire_conformant"] is False
    assert value["gpp_section"] == {"id": 7, "version": 2}
    assert value["fields"] == {
        "SaleOptOut": 0,
        "SharingOptOut": 1,
        "TargetedAdvertisingOptOut": 1,
    }
    assert value["gpc_subsection"] == {"Gpc": True}
    assert value["artifact_sha256"] == project_us_privacy_signals(
        _binding(contract),
        contract,
        _us_map(),
        USPrivacyChoices("not_applicable", "opted_out", "opted_out"),
        detect_privacy_headers({"Sec-GPC": "1"}),
    ).artifact_sha256


def test_us_projection_rejects_signal_choice_conflicts() -> None:
    contract = _contract()
    with pytest.raises(ContradictoryConsentSignalError, match="GPC"):
        project_us_privacy_signals(
            _binding(contract),
            contract,
            _us_map(),
            USPrivacyChoices("not_opted_out", "opted_out", "opted_out"),
            detect_privacy_headers({"Sec-GPC": "1"}),
        )
    with pytest.raises(ContradictoryConsentSignalError, match="DNT"):
        project_us_privacy_signals(
            _binding(contract),
            contract,
            _us_map(),
            USPrivacyChoices("not_applicable", "opted_out", "not_opted_out"),
            detect_privacy_headers({"DNT": "1"}),
        )


@pytest.mark.parametrize(
    "purpose_map",
    [
        _us_map(sharing_purpose_id="us.sale"),
        _us_map(sale_purpose_id="missing.purpose"),
        _us_map(vendor_ids=(9999,)),
        _us_map(vendor_ids=(755, 755)),
    ],
)
def test_us_projection_rejects_unbound_purpose_or_vendor_inputs(
    purpose_map: USPrivacyPurposeMap,
) -> None:
    contract = _contract()
    with pytest.raises(ConsentStandardsValidationError):
        project_us_privacy_signals(
            _binding(contract),
            contract,
            purpose_map,
            USPrivacyChoices("opted_out", "opted_out", "opted_out"),
            _no_signals(),
        )


@pytest.mark.parametrize(
    ("sections", "golden", "allow_deprecated"),
    [
        ((2,), "DBABM", False),
        ((2, 6), "DBACNY", True),
        ((5, 6), "DBABjw", True),
    ],
)
def test_gpp_v1_header_matches_official_golden_vectors(
    sections: tuple[int, ...],
    golden: str,
    allow_deprecated: bool,
) -> None:
    contract = _contract()
    encoded = encode_gpp_header(
        _binding(contract),
        contract,
        sections,
        allow_deprecated=allow_deprecated,
    )
    decoded = decode_gpp_header(
        golden,
        _binding(contract),
        contract,
        allow_deprecated=allow_deprecated,
    )

    assert encoded.encoded == golden
    assert decoded.section_ids == sections
    assert decoded.encoded == golden
    assert decoded.payload_sha256 == encoded.payload_sha256
    assert decoded.artifact_sha256 == encoded.artifact_sha256


@pytest.mark.parametrize(
    "sections",
    [
        (7,),
        (7, 8, 9),
        (2, 7, 9, 10, 27),
        tuple(range(7, 28)),
    ],
)
def test_gpp_header_round_trip_is_canonical(sections: tuple[int, ...]) -> None:
    contract = _contract()
    first = encode_gpp_header(_binding(contract), contract, sections)
    decoded = decode_gpp_header(first.encoded, _binding(contract), contract)
    reordered = encode_gpp_header(
        _binding(contract),
        contract,
        tuple(reversed(sections)),
    )

    assert decoded.section_ids == tuple(sorted(sections))
    assert decoded.as_dict()["scope"] == "gpp-header-only"
    assert reordered.encoded == first.encoded
    assert reordered.artifact_sha256 == first.artifact_sha256


@pytest.mark.parametrize(
    ("operation", "error"),
    [
        (lambda b, c: encode_gpp_header(b, c, (6,)), UnsupportedConsentStandardError),
        (lambda b, c: encode_gpp_header(b, c, (28,)), UnsupportedConsentStandardError),
        (lambda b, c: encode_gpp_header(b, c, (7, 7)), ConsentStandardsValidationError),
        (lambda b, c: decode_gpp_header("CBABM", b, c), ConsentStandardsIntegrityError),
        (lambda b, c: decode_gpp_header("DBABMA", b, c), ConsentStandardsIntegrityError),
        (lambda b, c: decode_gpp_header("not~header", b, c), ConsentStandardsValidationError),
    ],
)
def test_gpp_header_rejects_unsupported_or_noncanonical_inputs(
    operation: object,
    error: type[Exception],
) -> None:
    contract = _contract()
    with pytest.raises(error):
        operation(_binding(contract), contract)  # type: ignore[operator]


def test_google_consent_mode_v2_default_is_deterministic() -> None:
    contract = _contract()
    command = google_consent_mode_v2(
        _binding(contract),
        contract,
        phase="default",
        mappings=_google_mappings(),
        decisions=_google_decisions(),
        signals=_no_signals(),
        wait_for_update_ms=500,
        regions=("US-CA", "DE"),
    )
    reordered = google_consent_mode_v2(
        _binding(contract),
        contract,
        phase="default",
        mappings=tuple(reversed(_google_mappings())),
        decisions=tuple(reversed(_google_decisions())),
        signals=_no_signals(),
        wait_for_update_ms=500,
        regions=("DE", "US-CA"),
    )

    assert command.gtag_arguments() == (
        "consent",
        "default",
        {
            "ad_storage": "denied",
            "ad_user_data": "denied",
            "ad_personalization": "denied",
            "analytics_storage": "granted",
            "wait_for_update": 500,
            "region": ["DE", "US-CA"],
        },
    )
    assert command.artifact_sha256 == reordered.artifact_sha256
    assert command.mapping_sha256 == reordered.mapping_sha256
    assert command.decision_sha256 == reordered.decision_sha256


def test_google_consent_mode_v2_update_has_only_consent_values() -> None:
    contract = _contract()
    command = google_consent_mode_v2(
        _binding(contract),
        contract,
        phase="update",
        mappings=_google_mappings(),
        decisions=_google_decisions(
            ad_storage="granted",
            ad_user_data="granted",
            ad_personalization="granted",
        ),
        signals=_no_signals(),
    )

    assert command.phase == "update"
    assert command.parameters() == {
        "ad_storage": "granted",
        "ad_user_data": "granted",
        "ad_personalization": "granted",
        "analytics_storage": "granted",
    }


def test_google_mapping_rejects_gpc_and_dnt_contradictions() -> None:
    contract = _contract()
    with pytest.raises(ContradictoryConsentSignalError, match="GPC"):
        google_consent_mode_v2(
            _binding(contract),
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=_google_decisions(ad_user_data="granted"),
            signals=detect_privacy_headers({"Sec-GPC": "1"}),
        )
    with pytest.raises(ContradictoryConsentSignalError, match="DNT"):
        google_consent_mode_v2(
            _binding(contract),
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=_google_decisions(ad_personalization="granted"),
            signals=detect_privacy_headers({"DNT": "1"}),
        )


def test_google_mapping_accepts_privacy_consistent_gpc_state() -> None:
    contract = _contract()
    command = google_consent_mode_v2(
        _binding(contract),
        contract,
        phase="update",
        mappings=_google_mappings(),
        decisions=_google_decisions(),
        signals=detect_privacy_headers({"Sec-GPC": "1"}),
    )
    assert command.parameters()["ad_user_data"] == "denied"
    assert command.parameters()["ad_personalization"] == "denied"


def test_google_mapping_rejects_missing_duplicate_and_unused_inputs() -> None:
    contract = _contract()
    binding = _binding(contract)
    with pytest.raises(ConsentStandardsValidationError, match="missing exact"):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=_google_decisions()[:-1],
            signals=_no_signals(),
        )
    duplicate = _google_decisions() + (_google_decisions()[0],)
    with pytest.raises(ConsentStandardsValidationError, match="more than once"):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=duplicate,
            signals=_no_signals(),
        )
    contradictory = _google_decisions() + (
        PurposeDecision("google.ad_storage", (755,), "granted"),
    )
    with pytest.raises(ContradictoryConsentSignalError):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=contradictory,
            signals=_no_signals(),
        )
    unused = _google_decisions() + (PurposeDecision("us.sale", (755,), "denied"),)
    with pytest.raises(ConsentStandardsValidationError, match="unconsumed"):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=_google_mappings(),
            decisions=unused,
            signals=_no_signals(),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"phase": "later"},
        {"phase": "update", "wait_for_update_ms": 500},
        {"phase": "update", "regions": ("US-CA",)},
        {"phase": "default", "wait_for_update_ms": 0},
        {"phase": "default", "wait_for_update_ms": 10_001},
        {"phase": "default", "regions": ("us-ca",)},
        {"phase": "default", "regions": ("US-CA", "US-CA")},
    ],
)
def test_google_mapping_rejects_unknown_phase_and_invalid_defaults(
    kwargs: dict[str, object],
) -> None:
    contract = _contract()
    values: dict[str, object] = {
        "phase": "default",
        "mappings": _google_mappings(),
        "decisions": _google_decisions(),
        "signals": _no_signals(),
    }
    values.update(kwargs)
    with pytest.raises(ConsentStandardsValidationError):
        google_consent_mode_v2(  # type: ignore[arg-type]
            _binding(contract),
            contract,
            **values,
        )


def test_google_mapping_rejects_unknown_or_duplicate_contract_mappings() -> None:
    contract = _contract()
    binding = _binding(contract)
    unknown = _google_mappings()[:-1] + (
        GoogleConsentPurpose("unknown_storage", "google.analytics_storage", (755,)),
    )
    with pytest.raises(ConsentStandardsValidationError, match="unsupported"):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=unknown,
            decisions=_google_decisions(),
            signals=_no_signals(),
        )
    duplicate = _google_mappings()[:-1] + (
        GoogleConsentPurpose("ad_storage", "google.analytics_storage", (755,)),
    )
    with pytest.raises(ConsentStandardsValidationError, match="unique"):
        google_consent_mode_v2(
            binding,
            contract,
            phase="update",
            mappings=duplicate,
            decisions=_google_decisions(),
            signals=_no_signals(),
        )


def test_unsupported_wire_codecs_fail_closed_after_binding_validation() -> None:
    contract = _contract()
    binding = _binding(contract)
    projection = project_us_privacy_signals(
        binding,
        contract,
        _us_map(),
        USPrivacyChoices("opted_out", "opted_out", "opted_out"),
        _no_signals(),
    )

    with pytest.raises(UnsupportedConsentStandardError, match="TCF 2.3 wire encoding"):
        encode_tcf_v2_3(binding, contract)
    with pytest.raises(UnsupportedConsentStandardError, match="no TC String"):
        decode_tcf_v2_3("COwK6gaOwK6gaFmAAAENAPCAAAAAAAAAAAAAAAAAAAAA.IFMsv_Z_G__bXfb-", binding, contract)
    with pytest.raises(UnsupportedConsentStandardError, match="US-National"):
        encode_gpp_us_national_v2(projection, binding, contract)
    with pytest.raises(UnsupportedConsentStandardError, match="header codec"):
        decode_gpp_discrete_section(7, "BUENYMAUABA.ENAA2F.YA", binding, contract)


def test_unsupported_wire_boundary_rejects_free_text_and_stale_binding_first() -> None:
    expected = _contract()
    stale = _contract(vendor_list_version=90)

    with pytest.raises(ConsentStandardsValidationError, match="wire token"):
        decode_tcf_v2_3("not a consent string", _binding(expected), expected)
    with pytest.raises(StaleStandardsBindingError):
        decode_tcf_v2_3("valid-looking-token", _binding(stale), expected)


def test_projection_cannot_be_rebound_before_unsupported_wire_call() -> None:
    contract = _contract()
    projection = project_us_privacy_signals(
        _binding(contract),
        contract,
        _us_map(),
        USPrivacyChoices("opted_out", "opted_out", "opted_out"),
        _no_signals(),
    )
    other_binding = _binding(contract, policy_version=8)

    with pytest.raises(ConsentStandardsIntegrityError, match="different policy"):
        encode_gpp_us_national_v2(projection, other_binding, contract)
