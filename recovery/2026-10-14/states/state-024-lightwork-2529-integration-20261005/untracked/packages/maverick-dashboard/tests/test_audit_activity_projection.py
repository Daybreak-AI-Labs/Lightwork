from __future__ import annotations


def test_activity_log_shows_reviewed_summary_and_discloses_call_proofs_as_technical():
    from maverick.audit import TOOL_CALL_COMMITMENT_ALGORITHM, tool_call_commitment
    from maverick_dashboard.audit_actions_api import event_details, event_technical

    call_id = "0123456789abcdef01234567"
    event = {
        "kind": "tool_call",
        "model": "openai/gpt-example",
        "resources_reached": 1,
        "resource_fingerprints": ["0123456789abcdef"],
        "call_id": call_id,
        "call_commitment": tool_call_commitment(call_id=call_id),
        "call_commitment_algorithm": TOOL_CALL_COMMITMENT_ALGORITHM,
        "input_summary": '{"question": "Which region should the report cover?"}',
        "raw_arguments": {"access_token": "never-show-this"},
        "new_internal_field": "future-proofing must not auto-display this",
    }

    details = {key: text for key, _label, text in event_details(event)}
    technical = {key: text for key, _label, text in event_technical(event)}

    assert details == {"model": "openai/gpt-example", "resources_reached": "1"}
    assert "input_summary" not in details
    assert "raw_arguments" not in details
    assert "new_internal_field" not in details
    assert technical == {
        "resource_fingerprints": "0123456789abcdef",
        "call_id": call_id,
        "call_commitment": tool_call_commitment(call_id=call_id),
        "call_commitment_algorithm": TOOL_CALL_COMMITMENT_ALGORITHM,
    }


def test_unknown_audit_event_fields_are_not_customer_visible():
    from maverick_dashboard.audit_actions_api import event_details, event_technical

    event = {"kind": "a_future_event", "status": "done", "payload": {"private": "value"},
             "trace_id": "a" * 64}

    assert event_details(event) == []
    assert event_technical(event) == []


def test_metadata_projection_preserves_only_verified_call_integrity_details():
    from maverick.audit import TOOL_CALL_COMMITMENT_ALGORITHM, tool_call_commitment
    from maverick_dashboard.audit_actions_api import event_details, event_technical
    from maverick_dashboard.audit_search import search_events

    call_id = "0123456789abcdef01234567"
    commitment = tool_call_commitment(call_id=call_id)
    raw = {
        "kind": "tool_call",
        "model": "openai/gpt-example",
        "resources": ["customer-records"],
        "call_id": call_id,
        "call_commitment": commitment,
        "call_commitment_algorithm": TOOL_CALL_COMMITMENT_ALGORITHM,
        "input_summary": "private customer question",
        "raw_arguments": {"access_token": "never-show-this"},
        "future_internal_field": "not reviewed",
    }

    # The Activity page runs this projection while filtering its source rows.
    projected = search_events([raw], matches=lambda _event: True,
                              person_for=lambda _event: "operator", limit=10)["events"][0]
    details = {key: text for key, _label, text in event_details(projected)}
    technical = {key: text for key, _label, text in event_technical(projected)}

    assert details == {"model": "openai/gpt-example", "resources_reached": "1"}
    assert technical == {
        "call_id": call_id,
        "call_commitment": commitment,
        "call_commitment_algorithm": TOOL_CALL_COMMITMENT_ALGORITHM,
        "resource_fingerprints": "" + projected["resource_fingerprints"][0],
    }
    assert "private customer question" not in str(projected)
    assert "never-show-this" not in str(projected)
    assert "future_internal_field" not in projected

    tampered = search_events([{**raw, "call_commitment": "f" * 64}],
                             matches=lambda _event: True,
                             person_for=lambda _event: "operator", limit=10)["events"][0]
    tampered_technical = {key for key, _label, _text in event_technical(tampered)}
    assert not {"call_id", "call_commitment", "call_commitment_algorithm"} & tampered_technical

