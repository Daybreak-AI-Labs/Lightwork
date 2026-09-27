"""Proof of Oversight: canaries, earned authority, drills, cruxes, signed records."""
from __future__ import annotations

import pytest
from maverick import config
from maverick.immune import oversight as o
from maverick.world_model import WorldModel


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_immune", lambda: {"canaries": True})
    return WorldModel(tmp_path / "world.db")


def _canary(world, cls="payments"):
    return o.seed_canary(world, action="Pay INV-4411 to updated bank account",
                         detail="Vendor bank details changed 2h ago", decision_class=cls,
                         flaw="bank change + urgency", requested_by="agent:ap_clerk")


def test_config_default_is_off(monkeypatch):
    monkeypatch.setattr(config, "load_config", dict)
    assert config.get_immune() == {"canaries": False}
    monkeypatch.setattr(config, "load_config", lambda: {"immune": {"canaries": "yes"}})
    assert config.get_immune() == {"canaries": False}  # malformed stays off
    monkeypatch.setattr(config, "load_config", lambda: {"immune": {"canaries": True}})
    assert config.get_immune() == {"canaries": True}


def test_seeding_refused_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_immune", lambda: {"canaries": False})
    with pytest.raises(PermissionError):
        _canary(WorldModel(tmp_path / "w.db"))


def test_canary_is_indistinguishable_in_the_queue(world):
    cid = _canary(world)
    real = world.create_approval("Pay INV-4412", risk="medium", detail="Routine",
                                 requested_by="agent:ap_clerk")
    fields = {"action", "risk", "scope", "provenance", "status", "requested_by"}
    c, r = world.get_approval(cid), world.get_approval(real)
    assert {f: type(getattr(c, f)) for f in fields} == {f: type(getattr(r, f)) for f in fields}
    assert c.provenance == r.provenance and c.scope == r.scope
    assert o.canary_ids() == {cid}


def test_catches_and_misses_are_scored_per_person_and_class(world):
    a, b, c = _canary(world), _canary(world), _canary(world, "contracts")
    world.decide_approval(a, "denied", decided_by="alice")
    world.decide_approval(b, "approved", decided_by="alice")
    world.decide_approval(c, "denied", decided_by="bob")
    _canary(world)  # pending: not scored
    s = o.score(world)
    assert (s[("alice", "payments")].caught, s[("alice", "payments")].missed) == (1, 1)
    assert s[("bob", "contracts")].caught == 1


def test_authority_requires_evidence_not_luck():
    assert not o.authority(o.Stats(caught=3))             # 3/3 is not enough evidence
    assert o.authority(o.Stats(caught=40, missed=1))
    assert not o.authority(o.Stats(caught=30, missed=10))  # 75% misses the bar


def test_drills_are_scored_separately(world):
    o.record_drill("carol", "month_end_close", True)
    o.record_drill("carol", "month_end_close", False)
    assert o.score(world, "drill")[("carol", "month_end_close")].n == 2
    assert o.score(world) == {}


def test_canaries_never_feed_agent_graduation(world):
    from maverick.agent_autonomy import _coerce_approvals
    cid = _canary(world)
    real = world.create_approval("real", requested_by="agent:ap_clerk")
    ids = {r["id"] for r in _coerce_approvals(world)}
    assert real in ids and cid not in ids


def test_cruxes_are_the_claims_that_flip_the_decision():
    claims = ["vendor is on the approved list", "amount matches PO", "memo is polite"]

    def decide(false):  # approve only if the first two claims hold
        return not ({"vendor is on the approved list", "amount matches PO"} & false)

    assert o.find_cruxes(claims, decide) == claims[:2]


def test_signed_record_verifies_and_detects_tampering(world):
    from maverick.audit.signing import trusted_audit_public_keys
    rec = o.oversight_record(approver="alice", subject="approval:7", decision="approved",
                             cruxes=["amount matches PO"], stats=o.Stats(caught=9, missed=1))
    pub = trusted_audit_public_keys()[rec["signature"]["key_id"]]
    assert o.verify_record(rec, pub)
    rec["body"]["decision"] = "denied"
    assert not o.verify_record(rec, pub)
