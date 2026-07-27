from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import ai_evidence_gateway as gateway
from maverick.privacy_ops import RecordConflict


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _enable(monkeypatch, tenant: str = "gateway-test-tenant") -> None:
    monkeypatch.setenv("MAVERICK_EVIDENCE_GATEWAY", "1")
    monkeypatch.setenv("MAVERICK_CLIENT_ID", tenant)


def _policy(
    *,
    policy_id: str = "default",
    actor: str = "policy-admin",
    model: str = "model-v1",
    context: str = "context-v1",
    **kwargs,
):
    return gateway.upsert_policy(
        policy_id,
        actor=actor,
        model_sha256=_sha(model),
        context_sha256=_sha(context),
        **kwargs,
    )


def _deliver(
    *,
    generated_text: str = "Generated answer.",
    input_text: str = "Sensitive user question.",
    conversation_id: str = "conversation-1",
    idempotency_key: str = "turn-1",
    policy_id: str = "default",
    model: str = "model-v1",
    context: str = "context-v1",
    **kwargs,
):
    return gateway.deliver_text(
        generated_text,
        input_text=input_text,
        conversation_id=conversation_id,
        idempotency_key=idempotency_key,
        actor="delivery-service",
        policy_id=policy_id,
        model_sha256=_sha(model),
        context_sha256=_sha(context),
        **kwargs,
    )


def _citation() -> dict:
    url = gateway.FRAMEWORK_SOURCES["ec_article_50"]["url"]
    return {
        "source_name": "European Commission",
        "feed_url": url,
        "retrieval_url": url,
        "record_url": url,
        "retrieved_at": "2026-07-20T12:00:00Z",
        "content_sha256": _sha("official-record-content"),
        "payload_sha256": _sha("feed-payload"),
        "source_record_sha256": _sha("source-record"),
        "source_format": "html",
        "parser_version": "test-parser-v1",
        "acquisition": "official_feed",
        "acquired_by": "test-ingestor",
        "official_citation": "European Commission Article 50 guidance",
    }


def _impact(*, policy_id: str = "default"):
    return gateway.record_regulatory_impact(
        "ec-article-50-guidance-update",
        alert_revision=7,
        content_sha256=_sha("normalized-regulatory-change"),
        citations=[_citation()],
        affected_policy_ids=(policy_id,),
        affected_asset_ids=("model-asset-1",),
        control_ids=("eu_ai_act_article_50",),
        match_reasons=("enabled regime and control matched deterministically",),
        actor="regulatory-ingestor",
    )


def test_gateway_is_default_off_and_requires_explicit_tenant(monkeypatch):
    monkeypatch.delenv("MAVERICK_EVIDENCE_GATEWAY", raising=False)
    assert gateway.enabled() is False
    with pytest.raises(gateway.EvidenceGatewayDisabled):
        gateway.list_policies()

    monkeypatch.setenv("MAVERICK_EVIDENCE_GATEWAY", "1")
    monkeypatch.delenv("MAVERICK_CLIENT_ID", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="explicit tenant",
    ):
        gateway.list_policies()


def test_exact_text_receipt_is_hash_only_signed_and_revision_aligned(monkeypatch):
    _enable(monkeypatch)
    policy = _policy()
    assert policy["revision"] == 1
    assert policy["record_revision"] == policy["revision"]

    result = _deliver()
    receipt = result["receipt"]
    expected = (
        f"{gateway.DEFAULT_DISCLOSURE_TEXT}\n\nGenerated answer.\n\n"
        f"[lightwork-ai-generated; evidence-receipt={receipt['receipt_id']}]"
    )
    assert result["delivered_text"] == expected
    signed = receipt["signed_receipt"]
    assert signed["input_sha256"] == _sha("Sensitive user question.")
    assert signed["generated_output_sha256"] == _sha("Generated answer.")
    assert signed["delivered_output_sha256"] == _sha(expected)
    assert signed["policy_binding"]["record_revision"] == policy["revision"]
    assert receipt["policy_id"] == "default"
    assert receipt["model_sha256"] == _sha("model-v1")
    assert receipt["context_sha256"] == _sha("context-v1")
    assert receipt["conversation_sha256"] == _sha("conversation-1")
    assert receipt["modality"] == "text"
    assert receipt["sequence"] == 1
    assert receipt["assurance"] == {"status": "current", "reasons": []}

    persisted = json.dumps(receipt, sort_keys=True)
    assert "Sensitive user question." not in persisted
    assert "Generated answer." not in persisted
    assert expected not in persisted
    assert signed["stores_raw_interaction_content"] is False

    trust = gateway.trusted_public_keys()
    assert gateway.verify_interaction_receipt(
        receipt,
        trusted_public_keys=trust,
    )
    assert gateway.verify_interaction_receipt(receipt)
    tampered = copy.deepcopy(receipt)
    tampered["signed_receipt"]["delivered_output_sha256"] = "0" * 64
    assert not gateway.verify_interaction_receipt(
        tampered,
        trusted_public_keys=trust,
    )
    assert not gateway.verify_interaction_receipt(
        receipt,
        trusted_public_keys={},
    )


def test_conversation_order_idempotency_and_changed_risk_inputs_fail(monkeypatch):
    _enable(monkeypatch)
    _policy()
    first = _deliver()
    replay = _deliver()
    assert replay["idempotent_replay"] is True
    assert replay["delivered_text"] == first["delivered_text"]
    assert replay["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]

    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="different deepfake",
    ):
        _deliver(deepfake=True)
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="different model_sha256",
    ):
        _deliver(model="model-v2")
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="different policy_id",
    ):
        _deliver(policy_id="another-policy")

    second = _deliver(
        generated_text="Second answer.",
        input_text="Second question.",
        idempotency_key="turn-2",
    )
    assert second["receipt"]["sequence"] == 2
    assert second["delivered_text"].startswith(
        gateway.DEFAULT_DISCLOSURE_TEXT
    )
    # A retry stays reproducible even after the conversation advances.
    assert _deliver()["idempotent_replay"] is True


def test_every_delivery_gets_disclosure_and_caller_cannot_consume_it_early(
    monkeypatch,
):
    _enable(monkeypatch)
    _policy()
    hidden_preparatory = _deliver(human_interaction=True)
    assert hidden_preparatory["receipt"]["sequence"] == 1
    assert hidden_preparatory["delivered_text"].startswith(
        gateway.DEFAULT_DISCLOSURE_TEXT
    )

    first_human = _deliver(
        generated_text="First human-facing answer.",
        input_text="First human-facing question.",
        idempotency_key="turn-2",
        human_interaction=True,
    )
    assert first_human["receipt"]["sequence"] == 2
    assert first_human["delivered_text"].startswith(
        gateway.DEFAULT_DISCLOSURE_TEXT
    )
    assert gateway.verify_interaction_receipt(first_human["receipt"])

    later_human = _deliver(
        generated_text="Later human-facing answer.",
        input_text="Later human-facing question.",
        idempotency_key="turn-3",
        human_interaction=True,
    )
    assert later_human["receipt"]["sequence"] == 3
    assert later_human["delivered_text"].startswith(
        gateway.DEFAULT_DISCLOSURE_TEXT
    )

    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="human_interaction cannot be disabled",
    ):
        _deliver(
            generated_text="Machine-only internal output.",
            idempotency_key="turn-4",
            human_interaction=False,
        )


def test_delivery_caller_cannot_disable_ai_content_marking(monkeypatch):
    _enable(monkeypatch)
    _policy()
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="synthetic_content cannot be disabled",
    ):
        _deliver(synthetic_content=False)
    assert gateway.list_interaction_receipts() == []


def test_inline_exception_cannot_bypass_visible_marking(monkeypatch):
    _enable(monkeypatch)
    _policy()
    exception = {
        "decision_id": "decision-1",
        "reviewer": "not-a-governed-authority",
        "approved_at": "2026-07-20T12:00:00Z",
        "rationale": "Caller supplied.",
    }
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="inline exceptions are not accepted",
    ):
        _deliver(
            deepfake=True,
            editorial_exception=exception,
        )
    assert gateway.list_interaction_receipts() == []

    delivered = _deliver(deepfake=True)
    assert gateway.DEFAULT_VISIBLE_MARKER in delivered["delivered_text"]
    transparency = delivered["receipt"]["signed_receipt"]["transparency"]
    assert transparency["visible_marking_required"] is True
    assert transparency["visible_marking_applied"] is True


def test_unsupported_required_media_marking_fails_closed(monkeypatch):
    _enable(monkeypatch)
    _policy(supported_modalities=("text", "image"))
    with pytest.raises(
        gateway.UnsupportedMediaMarking,
        match="not implemented",
    ):
        _deliver(modality="image", deepfake=True)
    assert gateway.list_interaction_receipts() == []


def test_policy_model_and_context_changes_stale_old_receipts(monkeypatch):
    _enable(monkeypatch)
    first_policy = _policy()
    delivery = _deliver()
    updated = gateway.upsert_policy(
        "default",
        actor="policy-admin",
        expected_revision=first_policy["revision"],
        model_sha256=_sha("model-v2"),
        context_sha256=_sha("context-v2"),
    )
    assert updated["revision"] == 2
    assert updated["record_revision"] == 2
    assert updated["snapshot_count"] == 2

    stale = gateway.get_interaction_receipt(
        delivery["receipt"]["receipt_id"]
    )
    assert stale is not None
    assert stale["assurance"]["status"] == "stale"
    assert set(stale["assurance"]["reasons"]) >= {
        "stale_policy_revision",
        "stale_policy_digest",
        "stale_model",
        "stale_context",
    }
    # Historical snapshots retain only policy text and can reproduce exact
    # output bytes without retaining the generated output itself.
    replay = _deliver()
    assert replay["idempotent_replay"] is True
    assert replay["delivered_text"] == delivery["delivered_text"]


def test_retry_recovers_signed_receipt_after_governed_store_failure(monkeypatch):
    _enable(monkeypatch)
    first_policy = _policy()
    real_create = gateway._RECEIPTS.create
    failures = {"remaining": 1}

    def fail_once(record, *, action, actor):
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("simulated governed receipt persistence failure")
        return real_create(record, action=action, actor=actor)

    monkeypatch.setattr(gateway._RECEIPTS, "create", fail_once)
    with pytest.raises(
        RuntimeError,
        match="simulated governed receipt persistence failure",
    ):
        _deliver()

    ledger_rows = gateway._signed_rows(
        gateway._receipt_path(),
        "receipt_id",
        _stable_receipt_id := gateway._stable_id(
            "AIR",
            "gateway-test-tenant",
            _sha("conversation-1"),
            _sha("turn-1"),
        ),
    )
    assert len(ledger_rows) == 1
    assert gateway.list_interaction_receipts() == []
    assert not gateway._receipt_ledger_chain_valid([])

    # Recovery must use the signed row and its immutable policy snapshot even
    # if the current policy authority changes between the append and retry.
    gateway.upsert_policy(
        "default",
        actor="policy-admin",
        expected_revision=first_policy["revision"],
        disclosure_text="Updated disclosure.",
        model_sha256=_sha("model-v2"),
        context_sha256=_sha("context-v2"),
    )
    recovered = _deliver()
    assert recovered["idempotent_replay"] is True
    assert recovered["receipt"]["receipt_id"] == _stable_receipt_id
    assert recovered["receipt"]["signed_receipt"]["hash"] == ledger_rows[0]["hash"]
    assert (
        recovered["receipt"]["signed_receipt"]["issued_at"]
        == ledger_rows[0]["issued_at"]
    )
    assert recovered["delivered_text"].startswith(
        gateway.DEFAULT_DISCLOSURE_TEXT
    )
    assert gateway._receipt_ledger_chain_valid(
        gateway.list_interaction_receipts()
    )

    next_turn = _deliver(
        generated_text="Next answer.",
        input_text="Next question.",
        idempotency_key="turn-2",
        model="model-v2",
        context="context-v2",
    )
    assert next_turn["receipt"]["sequence"] == 2


def test_signing_identity_transaction_prevents_duplicate_rows(monkeypatch):
    _enable(monkeypatch)
    _policy()

    def call():
        return _deliver()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: call(), range(2)))
    assert {row["receipt"]["receipt_id"] for row in results} == {
        results[0]["receipt"]["receipt_id"]
    }
    assert sorted(row["idempotent_replay"] for row in results) == [False, True]
    rows = [
        json.loads(line)
        for line in gateway._receipt_path().read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["receipt_id"] == results[0]["receipt"]["receipt_id"]


def test_delivery_fails_closed_before_receipt_capacity_is_crossed(monkeypatch):
    _enable(monkeypatch)
    _policy()
    monkeypatch.setattr(gateway, "_MAX_RECEIPTS", 1)
    first = _deliver()

    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="receipt capacity has been reached",
    ):
        _deliver(
            generated_text="Second answer.",
            input_text="Second question.",
            idempotency_key="turn-2",
        )

    receipts = gateway.list_interaction_receipts(limit=1)
    assert [row["receipt_id"] for row in receipts] == [
        first["receipt"]["receipt_id"]
    ]
    assert len(gateway._signed_ledger_rows(gateway._receipt_path())) == 1
    conversation = gateway._CONVERSATIONS.get(
        gateway._stable_id("AIC", _sha("conversation-1"))
    )
    assert conversation is not None
    assert conversation["pending"] is None
    assert gateway.summary()["readiness"]["ready"] is True
    packet = gateway.render_assurance_packet(actor="assurance-officer")
    assert packet["verification"]["receipt_ledger_chain_valid"] is True
    assert gateway.verify_assurance_packet(packet)


def test_accepted_impact_refreshes_policy_and_stales_receipt(monkeypatch):
    _enable(monkeypatch)
    policy = _policy()
    delivery = _deliver()
    impact = _impact()
    accepted = gateway.review_regulatory_impact(
        impact["id"],
        decision="accepted",
        reviewer="risk-committee",
        rationale="Official cited change affects the enabled policy.",
        expected_revision=impact["revision"],
    )
    assert accepted["status"] == "accepted"
    assert accepted["review"]["human_review"] is True
    assert accepted["policy_refreshes"][0]["policy_record_revision"] == (
        policy["revision"] + 1
    )
    refreshed = gateway.get_policy()
    assert refreshed is not None
    assert refreshed["regulatory_bindings"][0]["impact_id"] == impact["id"]
    stale = gateway.get_interaction_receipt(
        delivery["receipt"]["receipt_id"]
    )
    assert stale is not None
    assert stale["assurance"]["status"] == "stale"
    with pytest.raises(RecordConflict):
        gateway.review_regulatory_impact(
            impact["id"],
            decision="dismissed",
            reviewer="other-reviewer",
            rationale="Concurrent dismissal must not overwrite acceptance.",
            expected_revision=impact["revision"],
        )


def test_policy_updates_cannot_forge_or_erase_regulatory_bindings(monkeypatch):
    _enable(monkeypatch)
    _policy()
    impact = _impact()
    gateway.review_regulatory_impact(
        impact["id"],
        decision="accepted",
        reviewer="risk-committee",
        rationale="Bind the cited, reviewed change.",
        expected_revision=impact["revision"],
    )
    current = gateway.get_policy()
    assert current is not None
    binding = current["regulatory_bindings"][0]

    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="only be changed by an accepted regulatory impact",
    ):
        gateway.upsert_policy(
            "default",
            actor="policy-admin",
            expected_revision=current["revision"],
            model_sha256=_sha("model-v1"),
            context_sha256=_sha("context-v1"),
            regulatory_bindings=[],
        )

    updated = gateway.upsert_policy(
        "default",
        actor="policy-admin",
        expected_revision=current["revision"],
        model_sha256=_sha("model-v2"),
        context_sha256=_sha("context-v2"),
    )
    assert updated["regulatory_bindings"] == [binding]


def test_acceptance_claim_is_recoverable_and_blocks_dismissal(monkeypatch):
    _enable(monkeypatch)
    _policy()
    impact = _impact()
    real_bind = gateway._bind_impact_to_policy

    def unavailable(*_args, **_kwargs):
        raise gateway.EvidenceGatewayStateError("policy backend unavailable")

    monkeypatch.setattr(gateway, "_bind_impact_to_policy", unavailable)
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="policy backend unavailable",
    ):
        gateway.review_regulatory_impact(
            impact["id"],
            decision="accepted",
            reviewer="risk-committee",
            rationale="Apply the cited change.",
            expected_revision=impact["revision"],
        )
    refreshing = gateway.list_regulatory_impacts()[0]
    assert refreshing["status"] == "accepted_refreshing"
    cockpit = gateway.summary()
    assert cockpit["refreshing_regulatory_impact_count"] == 1
    assert "regulatory_impacts_refreshing" in cockpit["readiness"]["gaps"]
    with pytest.raises(RecordConflict, match="already being applied"):
        gateway.review_regulatory_impact(
            impact["id"],
            decision="dismissed",
            reviewer="other-reviewer",
            rationale="Cannot race the claimed acceptance.",
            expected_revision=refreshing["revision"],
        )

    monkeypatch.setattr(gateway, "_bind_impact_to_policy", real_bind)
    recovered = gateway.review_regulatory_impact(
        impact["id"],
        decision="accepted",
        reviewer="risk-committee",
        rationale="Apply the cited change.",
        # The original revision is accepted only for the identical claimed
        # review, making a failed policy refresh retryable.
        expected_revision=impact["revision"],
    )
    assert recovered["status"] == "accepted"


def test_summary_treats_tampered_receipt_as_not_ready(monkeypatch):
    _enable(monkeypatch)
    _policy()
    delivery = _deliver()
    record = gateway._RECEIPTS.get(delivery["receipt"]["receipt_id"])
    assert record is not None

    def tamper(row):
        row["signed_receipt"]["delivered_output_sha256"] = "f" * 64

    gateway._RECEIPTS.update(
        record["id"],
        tamper,
        expected_revision=record["revision"],
        action="test_tamper",
        actor="test",
    )
    cockpit = gateway.summary()
    assert cockpit["unverified_interaction_receipt_count"] == 1
    assert cockpit["readiness"]["ready"] is False
    assert "unverified_interaction_receipts" in cockpit["readiness"]["gaps"]


def test_summary_and_packet_reject_missing_receipt_store_rows(monkeypatch):
    _enable(monkeypatch)
    _policy()
    _deliver(
        generated_text="First answer.",
        conversation_id="ledger-completeness",
        idempotency_key="turn-1",
    )
    _deliver(
        generated_text="Second answer.",
        conversation_id="ledger-completeness",
        idempotency_key="turn-2",
    )
    all_receipts = gateway.list_interaction_receipts()
    assert len(all_receipts) == 2
    assert gateway._receipt_ledger_chain_valid(all_receipts)

    # Simulate loss of the terminal governed-store row while the signed ledger
    # remains intact. The remaining conversation prefix and ledger are each
    # internally valid, but their exact identities no longer reconcile.
    remaining_prefix = [
        row for row in all_receipts if row["signed_receipt"]["sequence"] == 1
    ]
    assert gateway._conversation_chains_valid(remaining_prefix)
    assert not gateway._receipt_ledger_chain_valid(remaining_prefix)
    assert not gateway._receipt_ledger_chain_valid([])
    monkeypatch.setattr(
        gateway,
        "list_interaction_receipts",
        lambda *, limit=100: remaining_prefix,
    )

    cockpit = gateway.summary()
    assert cockpit["interaction_receipts"]["conversation_chains_valid"] is True
    assert cockpit["interaction_receipts"]["ledger_chain_valid"] is False
    assert "invalid_receipt_ledger_chain" in cockpit["readiness"]["gaps"]
    assert cockpit["readiness"]["ready"] is False

    packet = gateway.render_assurance_packet(actor="assurance-officer")
    assert packet["verification"]["receipt_ledger_chain_valid"] is False
    assert "invalid_receipt_ledger_chain" in packet["readiness"]["gaps"]
    assert packet["readiness"]["ready"] is False


def test_composite_packet_has_official_sources_and_external_verification(monkeypatch):
    _enable(monkeypatch)
    _policy()
    _deliver()
    packet = gateway.render_assurance_packet(actor="assurance-officer")
    assert packet["legal_certification"] is False
    assert packet["compliance_verdict"] == "not_provided"
    assert packet["stores_raw_interaction_content"] is False
    assert set(packet["framework_sources"]) == {
        "ec_article_50",
        "ec_gpai_guidelines",
        "nist_ai_rmf_1_0",
        "omb_m_25_22",
    }
    assert all(
        source["url"].startswith("https://")
        for source in packet["framework_sources"].values()
    )
    trust = gateway.trusted_public_keys()
    assert gateway.verify_assurance_packet(
        packet,
        trusted_public_keys=trust,
    )
    assert gateway.verify_assurance_packet(packet)
    assert not gateway.verify_assurance_packet(
        packet,
        trusted_public_keys={},
    )
    tampered = copy.deepcopy(packet)
    tampered["readiness"]["ready"] = not tampered["readiness"]["ready"]
    assert not gateway.verify_assurance_packet(
        tampered,
        trusted_public_keys=trust,
    )


def test_packet_rechecks_half_committed_receipt_before_attesting(monkeypatch):
    _enable(monkeypatch)
    _policy()
    delivery = _deliver()
    signed = delivery["receipt"]["signed_receipt"]
    orphan_event = {
        key: copy.deepcopy(value)
        for key, value in signed.items()
        if key not in {"prev_hash", "key_id", "hash", "sig"}
    }
    orphan_event.update(
        {
            "receipt_id": gateway._stable_id(
                "AIR",
                "gateway-test-tenant",
                _sha("packet-race-conversation"),
                _sha("packet-race-turn"),
            ),
            "issued_at": float(signed["issued_at"]) + 1.0,
            "conversation_sha256": _sha("packet-race-conversation"),
            "sequence": 1,
            "idempotency_sha256": _sha("packet-race-turn"),
            "parent_receipt_hash": "",
        }
    )
    orphan_event.pop("receipt_payload_sha256")
    orphan_event["receipt_payload_sha256"] = gateway._sha256(orphan_event)
    real_packet_json_size = gateway._packet_json_size
    injected = {"done": False}

    def append_after_initial_snapshot(value):
        if not injected["done"]:
            injected["done"] = True
            gateway._sign_event(
                orphan_event,
                path=gateway._receipt_path(),
                identity_key="receipt_id",
                identity=orphan_event["receipt_id"],
            )
        return real_packet_json_size(value)

    monkeypatch.setattr(
        gateway,
        "_packet_json_size",
        append_after_initial_snapshot,
    )
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="receipt ledger changed during packet snapshot",
    ):
        gateway.render_assurance_packet(actor="assurance-officer")
    assert not gateway._receipt_ledger_chain_valid(
        gateway.list_interaction_receipts()
    )


def test_historical_packet_accepts_a_later_trust_registry_superset(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    _enable(monkeypatch)
    _policy()
    _deliver()
    packet = gateway.render_assurance_packet(actor="assurance-officer")
    trust = gateway.trusted_public_keys()
    extra_private = Ed25519PrivateKey.generate()
    extra_public = extra_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    expanded_trust = {
        **trust,
        hashlib.sha256(extra_public).hexdigest()[:16]: extra_public.hex(),
    }
    assert gateway.verify_assurance_packet(
        packet,
        trusted_public_keys=expanded_trust,
    )


def test_packet_window_is_bounded_manifested_and_verifiable(monkeypatch):
    _enable(monkeypatch)
    _policy(policy_id="policy-a")
    _policy(policy_id="policy-b")
    _deliver(
        policy_id="policy-a",
        conversation_id="conversation-a",
        idempotency_key="turn-a",
    )
    _deliver(
        policy_id="policy-b",
        conversation_id="conversation-b",
        idempotency_key="turn-b",
    )
    monkeypatch.setattr(gateway, "_MAX_PACKET_POLICIES", 1)
    monkeypatch.setattr(gateway, "_MAX_PACKET_RECEIPTS", 1)
    packet = gateway.render_assurance_packet(actor="assurance-officer")
    window = packet["evidence_window"]
    assert window["source_counts"]["policies"] == 2
    assert window["source_counts"]["interaction_receipts"] == 2
    assert window["included_counts"]["policies"] == 1
    assert window["included_counts"]["interaction_receipts"] == 1
    assert "packet_window_truncated" in packet["readiness"]["gaps"]
    assert packet["readiness"]["ready"] is False
    assert gateway.verify_assurance_packet(packet)


def test_metadata_cannot_claim_raw_input_text(monkeypatch):
    _enable(monkeypatch)
    with pytest.raises(
        ValueError,
        match="unsupported non-governance fields",
    ):
        _policy(metadata={"input_text": "raw prompt"})


def test_corrupted_regulatory_impact_is_not_repackaged(monkeypatch):
    _enable(monkeypatch)
    _policy()
    impact = _impact()
    persisted = gateway._IMPACTS.get(impact["id"])
    assert persisted is not None

    def tamper(row):
        row["citations"][0]["content_sha256"] = "f" * 64

    gateway._IMPACTS.update(
        persisted["id"],
        tamper,
        expected_revision=persisted["revision"],
        action="test_tamper",
        actor="test",
    )
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="integrity validation",
    ):
        gateway.list_regulatory_impacts()


def test_seed_demo_is_idempotent_and_offline(monkeypatch):
    _enable(monkeypatch)
    first = gateway.seed_demo()
    second = gateway.seed_demo()
    assert first["synthetic"] is True
    assert first["network_access"] is False
    assert first["policy_id"] == second["policy_id"]
    assert first["receipt_id"] == second["receipt_id"]
    assert first["regulatory_impact_id"] == second["regulatory_impact_id"]
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert len(gateway.list_policies()) == 1
    assert len(gateway.list_interaction_receipts()) == 1
    assert len(gateway.list_regulatory_impacts()) == 1
    assert "synthetic_demo_data_present" in second["summary"]["readiness"]["gaps"]


def test_demo_seed_refuses_to_mix_with_live_tenant_evidence(monkeypatch):
    _enable(monkeypatch)
    _policy()
    with pytest.raises(
        gateway.EvidenceGatewayStateError,
        match="empty or dedicated demo tenant",
    ):
        gateway.seed_demo()
