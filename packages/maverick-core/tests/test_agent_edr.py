"""Tests for the Agent Security Plane (moonshot Bet 4).

Two failure modes matter more than the happy path, and most of these tests are
about them. Reporting *no detections* from a deployment whose defenses are off
trains an analyst to stop looking. Reporting *contained* when only half the
containment landed leaves a live agent that everyone believes is locked out.
Both are the same shape: a plane that says more than its evidence supports.
"""
from __future__ import annotations

import json

import pytest
from maverick import agent_edr as edr

crypto = pytest.importorskip(
    "cryptography", reason="detections come off the signed audit chain")


@pytest.fixture
def audit_dir(tmp_path):
    d = tmp_path / "audit"
    d.mkdir()
    return d


def _write(audit_dir, rows, day="2026-01-01"):
    from maverick.audit.signing import AuditSigner
    signer = AuditSigner(audit_dir / f"{day}.ndjson")
    for row in rows:
        assert signer.write(dict(row))
    return audit_dir / f"{day}.ndjson"


def _row(kind, **kw):
    base = {"ts": 1_700_000_000.0, "kind": kind, "agent": "worker-1"}
    base.update(kw)
    return base


class _Registry:
    """Stand-in for a live swarm's QuarantineRegistry."""

    def __init__(self, seal_works=True):
        self.sealed = set()
        self._seal_works = seal_works

    def seal(self, agent, reason):
        if self._seal_works:
            self.sealed.add(agent)

    def is_sealed(self, agent):
        return agent in self.sealed

    def unseal_agent(self, agent):
        self.sealed.discard(agent)


# ---- detect -----------------------------------------------------------------

def test_enforcement_rows_become_normalized_detections(audit_dir):
    _write(audit_dir, [
        _row("shield_block", reason="injection in tool schema"),
        _row("egress_blocked", reason="POST to unknown host"),
        _row("capability_denied", tool="wire_transfer", reason="outside grant"),
    ])
    found = edr.detections(audit_dir=audit_dir)
    assert {d.kind for d in found} == {
        edr.INJECTION, edr.EGRESS_BREACH, edr.CAPABILITY_BREACH}
    assert all(d.subject == "worker-1" for d in found)


def test_an_unmapped_event_kind_is_not_invented_into_a_threat(audit_dir):
    """Silence beats guessing. A plane that manufactures detections from rows
    it does not understand teaches its operator to ignore it."""
    _write(audit_dir, [
        _row("tool_call", name="read_file"),
        _row("goal_start"),
        _row("some_future_kind", reason="unknown"),
    ])
    assert edr.detections(audit_dir=audit_dir) == []


def test_severity_is_derived_not_self_declared(audit_dir):
    """A compromised agent gets no say in how loudly its detection rings."""
    _write(audit_dir, [_row("shield_block", severity="low", reason="nothing to see")])
    found = edr.detections(audit_dir=audit_dir)
    assert found[0].severity == edr.HIGH


def test_detections_are_newest_first(audit_dir):
    _write(audit_dir, [
        _row("shield_block", ts=100.0, reason="first"),
        _row("shield_block", ts=300.0, reason="third"),
        _row("shield_block", ts=200.0, reason="second"),
    ])
    found = edr.detections(audit_dir=audit_dir)
    assert [d.ts for d in found] == [300.0, 200.0, 100.0]


def test_filters_narrow_by_subject_severity_and_time(audit_dir):
    _write(audit_dir, [
        _row("shield_block", agent="a", ts=100.0),
        _row("governance_denied", agent="b", ts=200.0),
        _row("shield_block", agent="b", ts=300.0),
    ])
    assert len(edr.detections(subject="b", audit_dir=audit_dir)) == 2
    assert len(edr.detections(min_severity=edr.HIGH, audit_dir=audit_dir)) == 2
    assert len(edr.detections(since=250.0, audit_dir=audit_dir)) == 1


def test_a_row_with_no_attributable_principal_is_still_reported(audit_dir):
    """An unattributed detection is still a detection; dropping it would hide
    exactly the events an attacker would prefer nobody counted."""
    _write(audit_dir, [{"ts": 1.0, "kind": "shield_block", "reason": "x"}])
    found = edr.detections(audit_dir=audit_dir)
    assert found[0].subject == "(unattributed)"


def test_a_malformed_row_does_not_abandon_the_file(audit_dir):
    path = _write(audit_dir, [_row("shield_block", reason="real")])
    path.write_text("{not json\n" + path.read_text(encoding="utf-8"),
                    encoding="utf-8")
    assert len(edr.detections(audit_dir=audit_dir)) == 1


def test_a_missing_audit_directory_is_empty_not_an_error(tmp_path):
    assert edr.detections(audit_dir=tmp_path / "nope") == []


def test_the_timeline_is_incident_order(audit_dir):
    _write(audit_dir, [
        _row("shield_block", ts=300.0), _row("shield_block", ts=100.0)])
    assert [d.ts for d in edr.timeline("worker-1", audit_dir=audit_dir)] == [
        100.0, 300.0]


def test_summary_counts_by_class_severity_and_subject(audit_dir):
    _write(audit_dir, [
        _row("shield_block", agent="a"), _row("shield_block", agent="a"),
        _row("governance_denied", agent="b")])
    s = edr.summary(edr.detections(audit_dir=audit_dir))
    assert s["total"] == 3
    assert s["by_kind"][edr.INJECTION] == 2
    assert s["top_subjects"][0] == ("a", 2)


# ---- posture: the context every detection needs -----------------------------

def test_posture_names_the_defenses_that_are_off(monkeypatch):
    import maverick.capability as cap
    monkeypatch.setattr(cap, "capability_enforced", lambda: False)
    p = edr.posture()
    assert "capabilities" in p.blind_spots
    assert any("not watched" in n for n in p.notes)


def test_posture_reads_the_real_enforcement_authorities(monkeypatch):
    """A posture that disagreed with what is actually enforced is worse than
    none, so it asks the same functions the hot path asks."""
    import maverick.capability as cap
    monkeypatch.setattr(cap, "capability_enforced", lambda: True)
    assert edr.posture().capabilities is True


def test_a_quiet_chain_under_blind_defenses_is_not_a_clean_bill(audit_dir,
                                                                monkeypatch):
    import maverick.capability as cap
    monkeypatch.setattr(cap, "capability_enforced", lambda: False)
    report = edr.incident_report("worker-1", audit_dir=audit_dir)
    text = edr.render_report(report)
    assert "no detections" in text
    assert "defenses OFF" in text
    assert "proves nothing" in text


def test_an_unsigned_chain_is_flagged_in_the_report(audit_dir):
    """Flagged from the rows actually read. An earlier version asked the posture
    whether `cryptography` was importable, which answers a different question
    and would call bare rows tamper-evident on any machine with the library."""
    (audit_dir / "2026-01-01.ndjson").write_text(
        json.dumps({"ts": 1.0, "kind": "shield_block", "agent": "w",
                    "reason": "x"}) + "\n", encoding="utf-8")
    text = edr.render_report(edr.incident_report("w", audit_dir=audit_dir))
    assert "UNSIGNED" in text
    assert "not tamper-evident" in text


# ---- respond ----------------------------------------------------------------

def test_containment_seals_the_live_swarm_and_revokes_authority(audit_dir):
    reg = _Registry()
    result = edr.contain("worker-1", reason="honeytoken", registry=reg)
    assert result.contained
    assert result.sealed and reg.is_sealed("worker-1")
    assert result.revoked == ["worker-1"]


def test_a_run_scoped_seal_is_never_reported_as_durable():
    """Compartment seals live in a running swarm's memory; revocations are on
    disk. Blurring them would tell an operator an agent is locked out when the
    seal evaporates at process exit."""
    result = edr.contain("worker-1", reason="x", registry=_Registry())
    assert result.sealed is True
    assert result.seal_is_durable is False
    assert any("run-scoped" in n for n in result.notes)


def test_containment_without_a_live_swarm_says_nothing_was_sealed():
    result = edr.contain("worker-1", reason="x")
    assert result.sealed is False
    assert any("not sealed" in n for n in result.notes)
    assert result.contained  # the revocation still landed


def test_a_seal_that_does_not_take_effect_fails_the_containment():
    result = edr.contain("worker-1", reason="x", registry=_Registry(seal_works=False))
    assert not result.contained
    assert any("did not take effect" in f for f in result.failed)


def test_revocation_is_confirmed_rather_than_assumed(monkeypatch):
    """A write that silently did not land must not be reported as containment."""
    import maverick.revocation as rev

    class _Fake:
        def revoke(self, principal, *, reason="", now=None):
            return None
        def revoke_subtree(self, principal, edges, *, reason="", now=None):
            return [principal]
        def is_revoked(self, principal):
            return False
    monkeypatch.setattr(rev, "shared", lambda: _Fake())
    result = edr.contain("worker-1", reason="x")
    assert not result.contained
    assert any("did not take effect" in f for f in result.failed)


def test_containment_revokes_the_whole_capability_subtree():
    result = edr.contain("parent", reason="x",
                         edges={"parent": ["child-a", "child-b"]})
    assert set(result.revoked) == {"parent", "child-a", "child-b"}
    from maverick.revocation import is_revoked
    assert is_revoked("child-b")


def test_containment_is_idempotent():
    first = edr.contain("worker-1", reason="x", registry=_Registry())
    second = edr.contain("worker-1", reason="x again", registry=_Registry())
    assert first.contained and second.contained


def test_an_invalid_subject_is_refused():
    result = edr.contain("", reason="x")
    assert not result.contained
    assert "invalid subject" in result.failed


def test_release_reverses_a_containment():
    reg = _Registry()
    edr.contain("worker-1", reason="x", registry=reg)
    result = edr.release("worker-1", registry=reg)
    assert result.contained
    assert not reg.is_sealed("worker-1")
    from maverick.revocation import is_revoked
    assert not is_revoked("worker-1")


def test_releasing_something_never_contained_says_so():
    result = edr.release("never-touched")
    assert any("was not revoked" in n for n in result.notes)


def test_containment_lands_on_the_signed_chain(tmp_path, monkeypatch):
    seen = {}
    import maverick.audit as audit
    monkeypatch.setattr(audit, "record",
                        lambda kind, **kw: seen.update({"kind": kind, **kw}))
    edr.contain("worker-1", reason="honeytoken tripped", registry=_Registry())
    assert seen["kind"] == "agent_edr_containment"
    assert seen["subject"] == "worker-1"
    assert seen["contained"] is True


def test_an_audit_failure_never_blocks_containment(monkeypatch):
    """Containment is the safety act; telemetry must not be able to veto it."""
    import maverick.audit as audit

    def _boom(*a, **k):
        raise RuntimeError("audit sink down")
    monkeypatch.setattr(audit, "record", _boom)
    assert edr.contain("worker-1", reason="x", registry=_Registry()).contained


# ---- prove ------------------------------------------------------------------

def test_the_incident_report_carries_posture_timeline_and_state(audit_dir):
    _write(audit_dir, [
        _row("shield_block", ts=100.0, reason="injection"),
        _row("egress_blocked", ts=200.0, reason="exfil attempt")])
    edr.contain("worker-1", reason="incident")
    report = edr.incident_report("worker-1", audit_dir=audit_dir, now=5.0)
    assert report["currently_revoked"] is True
    assert [d["ts"] for d in report["detections"]] == [100.0, 200.0]
    assert report["summary"]["total"] == 2
    assert "posture" in report


def test_the_report_renders_without_detections(audit_dir):
    text = edr.render_report(edr.incident_report("nobody", audit_dir=audit_dir))
    assert "no detections in the attested window" in text


def test_detection_serializes_round_trip(audit_dir):
    _write(audit_dir, [_row("shield_block", reason="x", goal_id=7)])
    d = edr.detections(audit_dir=audit_dir)[0]
    payload = json.loads(json.dumps(d.to_dict()))
    assert payload["kind"] == edr.INJECTION
    assert payload["evidence"]["goal_id"] == 7


def test_containment_serializes_for_the_console():
    payload = edr.contain("w", reason="x", registry=_Registry()).to_dict()
    assert payload["contained"] is True
    assert payload["seal_is_durable"] is False


# ---- bounds, completeness and vacuous success -------------------------------
#
# Three defects a re-read of this module turned up, each the same shape as the
# ones it was written to prevent: a report that says more than it has.

def test_the_sweep_bound_keeps_the_newest_detections(audit_dir):
    """Day-files sort ascending, so an oldest-first sweep would fill the bound
    with history and silently drop the incident actually in progress."""
    for i, day in enumerate(("2026-01-01", "2026-01-02", "2026-01-03")):
        _write(audit_dir, [_row("shield_block", ts=100.0 + i,
                                reason=f"day{i}")] * 4, day=day)
    swept = edr.scan(audit_dir=audit_dir, limit=5)
    assert len(swept.found) == 5
    assert "day2" in {d.detail for d in swept.found}


def test_a_truncated_sweep_says_so(audit_dir):
    """A capped list that looks complete is how an analyst concludes they have
    seen everything."""
    _write(audit_dir, [_row("shield_block", reason="x")] * 6)
    assert edr.scan(audit_dir=audit_dir, limit=3).truncated is True
    assert edr.scan(audit_dir=audit_dir, limit=99).truncated is False


def test_the_bound_holds_across_day_files(audit_dir):
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        _write(audit_dir, [_row("shield_block", reason="x")] * 4, day=day)
    assert len(edr.scan(audit_dir=audit_dir, limit=5).found) == 5


def test_truncation_is_surfaced_in_the_report(audit_dir):
    _write(audit_dir, [_row("shield_block", agent="w", reason="x")] * 4)
    report = edr.incident_report("w", audit_dir=audit_dir)
    report["truncated"] = True
    assert "OLDER detections exist" in edr.render_report(report)


def test_signed_evidence_is_judged_from_the_rows_not_the_library(audit_dir):
    """`cryptography` being importable says nothing about whether signing was
    switched on. Bare rows must not be presented as tamper-evident."""
    (audit_dir / "2026-01-01.ndjson").write_text(
        json.dumps({"ts": 1.0, "kind": "shield_block", "agent": "w",
                    "reason": "x"}) + "\n", encoding="utf-8")
    swept = edr.scan(audit_dir=audit_dir)
    assert swept.unsigned_rows == 1
    assert swept.evidence_is_signed is False
    report = edr.incident_report("w", audit_dir=audit_dir)
    assert report["evidence_is_signed"] is False
    assert "not tamper-evident" in edr.render_report(report)


def test_signed_rows_are_recognized_as_signed(audit_dir):
    _write(audit_dir, [_row("shield_block", agent="w", reason="x")])
    swept = edr.scan(audit_dir=audit_dir)
    assert swept.unsigned_rows == 0
    assert swept.evidence_is_signed is True


def test_an_unreadable_day_makes_the_timeline_partial(audit_dir, monkeypatch):
    """A day we cannot read is a hole in the evidence, not an absence of
    detections."""
    _write(audit_dir, [_row("shield_block", agent="w", reason="x")])
    import maverick.audit.sealing as sealing

    def _boom(path, **kw):
        raise RuntimeError("sealed")
    monkeypatch.setattr(sealing, "segment_text", _boom)
    swept = edr.scan(audit_dir=audit_dir)
    assert swept.unreadable_days == ["2026-01-01"]
    assert swept.evidence_is_signed is False


def test_a_containment_asked_to_do_nothing_is_not_containment():
    """An empty failure list reading as success is the same laundering the rest
    of the module refuses."""
    result = edr.contain("victim", reason="x", seal=False, revoke=False)
    assert not result.contained
    assert "no containment action requested" in result.failed
    assert result.revoked == []


def test_a_hostile_detail_cannot_paint_a_fake_row_in_the_report(audit_dir):
    """Detail text is attacker-influenced. Left with its newlines it renders as
    extra report lines, so a crafted `reason` can paint a convincing
    `[low] FAKE CLEARED` row straight into the analyst's output."""
    _write(audit_dir, [_row(
        "shield_block", agent="w",
        reason="real\n  [low     ] FAKE CLEARED  nothing to see here")])
    det = edr.detections(audit_dir=audit_dir)[0]
    assert "\n" not in det.detail
    body = edr.render_report(edr.incident_report("w", audit_dir=audit_dir))
    fake_lines = [ln for ln in body.splitlines() if "FAKE CLEARED" in ln]
    assert len(fake_lines) == 1
    assert "real" in fake_lines[0]  # inline in the real row, not its own line


def test_a_hostile_subject_is_flattened_too(audit_dir):
    _write(audit_dir, [{"ts": 1.0, "kind": "shield_block",
                        "agent": "a\nb\tc", "reason": "x"}])
    assert edr.detections(audit_dir=audit_dir)[0].subject == "a b c"
