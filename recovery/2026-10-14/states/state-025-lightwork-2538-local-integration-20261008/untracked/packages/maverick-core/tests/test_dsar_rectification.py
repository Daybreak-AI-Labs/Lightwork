from __future__ import annotations

import pytest
from maverick.dsar import _fact_subject_token
from maverick.dsar_rectification import (
    DsarRectificationError,
    apply_subject_correction,
    preview_subject_correction,
    search_subject_records,
    subject_channels,
)
from maverick.world_model import WorldModel


def _world(path):
    world = WorldModel(path)
    alice = world.get_or_create_conversation("email", "alice@example.com")
    alice_turn = world.append_turn(alice.id, "user", "My name is Jon Smith.")
    bob = world.get_or_create_conversation("email", "bob@example.com")
    world.append_turn(bob.id, "user", "My name is Jon Smith.")
    other_channel = world.get_or_create_conversation("slack", "alice@example.com")
    world.append_turn(other_channel.id, "user", "My name is Jon Smith.")
    world.upsert_fact(
        f"user:{_fact_subject_token('email', 'alice@example.com')}:display_name", "Jon Smith",
        source="requester", trust_tier=2, sensitivity="personal", tags=["name"],
    )
    world.upsert_fact(
        f"user:{_fact_subject_token('email', 'bob@example.com')}:display_name", "Jon Smith",
    )
    return world, alice_turn


def test_search_limits_results_to_exact_subject_and_channel(tmp_path):
    world, _ = _world(tmp_path / "world.db")
    try:
        result = search_subject_records(world, "alice@example.com", "email", "Jon Smith")
        assert len(result["matches"]) == 2
        assert {row["kind"] for row in result["matches"]} == {"message", "personal fact"}
        assert result["more_available"] is False
        assert search_subject_records(world, "alice@example.com", "slack", "Jon Smith")["matches"][0]["kind"] == "message"
        assert all("bob" not in row["snippet"] for row in result["matches"])
    finally:
        world.close()


def test_account_choices_include_only_channels_for_the_exact_requester(tmp_path):
    world, _ = _world(tmp_path / "world.db")
    try:
        result = subject_channels(world, "alice@example.com")
        assert result == {"channels": ["email", "slack"], "more_available": False}
        bounded = subject_channels(world, "alice@example.com", limit=1)
        assert bounded == {"channels": ["email"], "more_available": True}
    finally:
        world.close()


def test_preview_and_apply_recheck_content_and_preserve_other_subjects(tmp_path):
    world, turn_id = _world(tmp_path / "world.db")
    try:
        found = search_subject_records(world, "alice@example.com", "email", "Jon Smith")
        message = next(row for row in found["matches"] if row["kind"] == "message")
        preview = preview_subject_correction(
            world, "alice@example.com", "email", reference=message["reference"],
            incorrect_text="Jon Smith", replacement_text="John Smith",
            expected_sha256=message["current_sha256"],
        )
        assert preview["before"] == "My name is Jon Smith."
        assert preview["after"] == "My name is John Smith."
        result = apply_subject_correction(
            world, "alice@example.com", "email", reference=message["reference"],
            incorrect_text="Jon Smith", replacement_text="John Smith",
            expected_sha256=message["current_sha256"],
        )
        assert result["matches_changed"] == 1
        assert world.recent_turns(1, limit=10)[0].content == "My name is John Smith."
        assert world.recent_turns(2, limit=10)[0].content == "My name is Jon Smith."
        assert world.recent_turns(3, limit=10)[0].content == "My name is Jon Smith."
        assert turn_id == 1
    finally:
        world.close()


def test_personal_fact_correction_keeps_its_scope_and_metadata(tmp_path):
    world, _ = _world(tmp_path / "world.db")
    try:
        found = search_subject_records(world, "alice@example.com", "email", "Jon Smith")
        fact = next(row for row in found["matches"] if row["kind"] == "personal fact")
        result = apply_subject_correction(
            world, "alice@example.com", "email", reference=fact["reference"],
            incorrect_text="Jon Smith", replacement_text="John Smith",
            expected_sha256=fact["current_sha256"],
        )
        assert result["matches_changed"] == 1
        alice_key = f"user:{_fact_subject_token('email', 'alice@example.com')}:display_name"
        bob_key = f"user:{_fact_subject_token('email', 'bob@example.com')}:display_name"
        corrected = world.fact_record(alice_key)
        assert corrected["value"] == "John Smith"
        assert corrected["source"] == "requester"
        assert corrected["trust_tier"] == 2
        assert corrected["sensitivity"] == "personal"
        assert corrected["tags"] == ["name"]
        assert world.fact_record(bob_key)["value"] == "Jon Smith"
    finally:
        world.close()


def test_stale_or_foreign_record_reference_is_refused(tmp_path):
    world, _ = _world(tmp_path / "world.db")
    try:
        alice = search_subject_records(world, "alice@example.com", "email", "Jon Smith")["matches"][0]
        world.append_turn(1, "assistant", "Changed while the correction was open.")
        with pytest.raises(DsarRectificationError, match="changed"):
            preview_subject_correction(
                world, "alice@example.com", "email", reference=alice["reference"],
                incorrect_text="Jon Smith", replacement_text="John Smith",
                expected_sha256="0" * 64,
            )
        bob = search_subject_records(world, "bob@example.com", "email", "Jon Smith")["matches"][0]
        with pytest.raises(DsarRectificationError, match="no longer available"):
            preview_subject_correction(
                world, "alice@example.com", "email", reference=bob["reference"],
                incorrect_text="Jon Smith", replacement_text="John Smith",
                expected_sha256=bob["current_sha256"],
            )
    finally:
        world.close()
