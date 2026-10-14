"""Regression tests for precise queue and external-effect claims."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_external_effect_contract_defines_all_authoritative_states() -> None:
    contract = (ROOT / "docs/specs/external-effect-semantics.md").read_text(
        encoding="utf-8"
    )
    for state in ("PREPARED", "COMMITTED", "REFUSED", "INDETERMINATE"):
        assert f"`{state}`" in contract
    assert "single winner" in contract
    assert "at-least-once under recovery" in contract
    assert "does not claim universal exactly-once execution" in contract


def test_current_execution_claim_surfaces_are_not_universal() -> None:
    claim_surfaces = (
        "CLAUDE.md",
        "STRESS_TEST.md",
        "docs/FEATURES.md",
        "docs/evidence/README.md",
        "packages/maverick-dashboard/maverick_dashboard/automation_queue.py",
        "packages/maverick-dashboard/maverick_dashboard/app.py",
    )
    forbidden = (
        "JobQueue exactly-once",
        "JobQueue's exactly-once",
        "zero-loss, exactly-once",
        "zero loss** and **exactly-once",
        "durable, retrying, exactly-once",
        "DB-level exactly-once",
    )
    for relative in claim_surfaces:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{relative} makes an overbroad claim: {phrase}"
