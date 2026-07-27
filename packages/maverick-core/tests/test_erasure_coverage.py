"""Every goal-linked table must be adjudicated for GDPR Art. 17 erasure.

``verify_erasure`` certifies ``clean: True`` when every count it takes is zero.
Its counts come from ``dsar.export_subject_data`` (conversations, turns, goals,
episodes, facts, audit_events) plus facts and knowledge chunks. Tables the
export does not know about are not counted -- so residual personal data in one
of them is reported as a clean erasure.

Derived from the live schema at ``PRAGMA foreign_key_list``, not from a list,
because a list is what goes stale: adding a table with a ``goal_id`` FK
silently widened the gap and nothing failed. Now it fails here until somebody
decides which side the table is on.

This test asserts COVERAGE, not correctness of the cascade itself. It cannot
tell you the erase works; it can tell you when a store has been added that
nobody has thought about, which is the failure mode that actually occurred.
"""

from __future__ import annotations

import sqlite3

import pytest

#: Goal-linked tables whose rows are counted by ``verify_erasure`` today, via
#: ``dsar.export_subject_data``.
COUNTED_BY_VERIFY = {
    "goals",      # counts["goals"]
    "turns",      # counts["turns"]
    "episodes",   # counts["episodes"]
}

#: Goal-linked tables deliberately NOT counted, each with the reason. These are
#: claims about the data, and a wrong one here is a compliance defect -- so they
#: are written down where a reviewer sees them rather than left implicit.
#:
#: NOTE: every entry below is currently a GAP, not a justification. They are
#: recorded as known-uncounted so the set is visible and reviewable; closing
#: them requires extending verify_erasure to count residual rows joined to the
#: subject's goals. That is the remaining half of this fix.
KNOWN_UNCOUNTED = {
    "artifacts": "deliverables produced by a subject's goal; may embed PII",
    "attachments": "files a subject uploaded; almost certainly PII",
    "goal_events": "state transitions; may carry subject text in payloads",
    "goal_origins": "what triggered the goal; may name the subject",
    "messages": "channel messages; PII by construction",
    "processed_messages": "dedupe ledger keyed by channel message id",
    "questions": "clarifying questions and answers; may carry PII",
    "share_links": "share tokens scoped to a subject's goal",
    "signoffs": "approver identity and comments",
}


def _goal_linked_tables(db_path) -> dict[str, list[str]]:
    con = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        refs: dict[str, list[str]] = {}
        for t in tables:
            for row in con.execute(f"PRAGMA foreign_key_list({t})"):
                if row[2] == "goals":
                    refs.setdefault(t, []).append(row[3])
        return refs
    finally:
        con.close()


@pytest.fixture()
def schema(tmp_path, monkeypatch):
    """A freshly created world DB.

    The path comes from open_world(explicit), NOT world_model.DEFAULT_DB:
    DEFAULT_DB is a module-level constant bound at import, so once another test
    has imported world_model it no longer follows MAVERICK_HOME and this
    fixture would inspect whatever database happened to exist. That made these
    assertions pass alone and fail in a full run -- an order-dependent test of
    a compliance property is worse than none.
    """
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    from maverick import world_model

    db = tmp_path / "world.db"
    world_model.open_world(db)
    return db


def test_the_scan_finds_the_goal_graph(schema) -> None:
    """Anti-vacuity: an empty FK graph would make everything below pass."""
    refs = _goal_linked_tables(schema)
    assert len(refs) >= 10, sorted(refs)


def test_every_goal_linked_table_is_adjudicated(schema) -> None:
    """A new goal-linked store must be classified before it can ship.

    Silence here previously meant "not counted", which reads to
    ``verify_erasure`` as "zero residual" and to an auditor as "erased".
    """
    refs = _goal_linked_tables(schema)
    adjudicated = COUNTED_BY_VERIFY | set(KNOWN_UNCOUNTED)
    unclassified = sorted(set(refs) - adjudicated)
    assert not unclassified, (
        f"{unclassified} reference goals but are neither counted by "
        "verify_erasure nor recorded in KNOWN_UNCOUNTED. An uncounted store "
        "makes `maverick erase-verify` certify CLEAN over data that is still "
        "there. Add it to verify_erasure's counts, or to KNOWN_UNCOUNTED with "
        "the reason it holds no subject data."
    )


def test_the_uncounted_set_is_not_quietly_growing(schema) -> None:
    """Every recorded gap must still be a real table.

    A stale entry hides that somebody fixed one, and makes the debt look larger
    than it is; a table that vanished should be removed from the record.
    """
    refs = _goal_linked_tables(schema)
    stale = sorted(set(KNOWN_UNCOUNTED) - set(refs))
    assert not stale, (
        f"{stale} are recorded as uncounted goal-linked tables but no longer "
        "reference goals. Remove them from KNOWN_UNCOUNTED.")


def test_counted_tables_really_are_goal_linked(schema) -> None:
    refs = _goal_linked_tables(schema)
    assert set(refs) >= COUNTED_BY_VERIFY, sorted(COUNTED_BY_VERIFY - set(refs))


def test_the_coverage_gap_is_recorded_with_its_size(schema) -> None:
    """State the number rather than leaving it to be counted by hand.

    Nine of twelve goal-linked tables are invisible to the erasure verifier.
    If that ratio improves, this fails and the constant gets updated -- which
    is the point: progress should require an edit, not go unnoticed.
    """
    refs = _goal_linked_tables(schema)
    uncounted = set(refs) - COUNTED_BY_VERIFY
    assert len(uncounted) == len(KNOWN_UNCOUNTED), (
        f"goal-linked tables not counted by verify_erasure: {sorted(uncounted)} "
        f"({len(uncounted)} of {len(refs)}). KNOWN_UNCOUNTED records "
        f"{len(KNOWN_UNCOUNTED)}. Reconcile them.")


def test_verify_erasure_does_not_claim_to_cover_what_it_does_not(schema) -> None:
    """The docstring must not promise coverage the counts do not deliver."""
    from maverick import erasure_verify

    doc = (erasure_verify.__doc__ or "") + (erasure_verify.verify_erasure.__doc__ or "")
    assert "every store Lightwork holds" not in doc, (
        "the module claims total coverage while nine goal-linked tables are "
        "uncounted. Say what is actually checked.")
    # And it must name the gap, not merely avoid overclaiming it away.
    assert "not counted" in doc or "**not** counted" in doc, (
        "the scope limit must be stated where a reader will find it")
