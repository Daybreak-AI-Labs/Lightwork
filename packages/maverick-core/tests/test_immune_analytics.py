"""Invariance, reliability/bonds/truth market, epistemic hygiene, policy
discovery, learning envelopes and pricing parallelism."""
from __future__ import annotations

import pytest
from maverick.immune import envelope, epistemic, invariance, market, policy, reliability


def test_awareness_gap_separates_signal_from_noise():
    loud = invariance.awareness_gap([False] * 95 + [True] * 5, [False] * 70 + [True] * 30)
    assert loud["gap"] == pytest.approx(0.25) and loud["significant"]
    quiet = invariance.awareness_gap([True, False] * 10, [True, False] * 10)
    assert quiet["gap"] == 0 and not quiet["significant"]
    with pytest.raises(ValueError):
        invariance.awareness_gap([], [True])


def test_drift_detects_a_silent_model_swap():
    before = {f"p{i}": "Refuse: policy 4.2" for i in range(10)}
    same = invariance.drift(before, dict.fromkeys(before, "  refuse:   POLICY 4.2"))
    assert same == {"agreement": 1.0, "changed": [], "drifted": False}
    swapped = invariance.drift(before, {**before, "p1": "Sure, here it is", "p7": "ok"})
    assert swapped["changed"] == ["p1", "p7"] and swapped["drifted"]


def test_bond_shrinks_only_with_real_track_record():
    assert reliability.bond(1000, []) == 1000
    few = reliability.bond(1000, [True] * 3)
    many = reliability.bond(1000, [True] * 300)
    assert many < few < 1000 and many > 0
    with pytest.raises(ValueError):
        reliability.bond(-1, [True])


def test_truth_market_verifies_contested_claims_first_and_settles():
    m = {"agreed": [("a", True, 5.0), ("b", True, 5.0)],
         "split": [("a", True, 5.0), ("b", False, 4.0)],
         "lopsided": [("a", True, 9.0), ("b", False, 1.0)]}
    assert reliability.allocate(m, 2) == ["split", "lopsided"]
    assert reliability.settle(m["split"], truth=False) == {"a": -5.0, "b": 5.0}
    assert reliability.settle(m["agreed"], truth=False) == {"a": 0.0, "b": 0.0}


def test_quarantine_and_unverified_basis():
    facts = {
        "f1": {"source": "agent", "verified": False},
        "f2": {"source": "human", "verified": False, "derived_from": ["f1"]},
        "f3": {"source": "agent", "verified": True, "derived_from": ["f1"]},
        "f4": {"source": "human", "verified": False, "derived_from": ["f3"]},
        "f5": {"source": "external", "verified": False, "derived_from": ["ghost"]},
        "c1": {"source": "human", "verified": False, "derived_from": ["c2"]},
        "c2": {"source": "human", "verified": False, "derived_from": ["c1"]},
    }
    assert epistemic.quarantine(facts, "f1") == ["f2", "f3", "f4"]
    # f3 was verified by a human: a firebreak for f4. Unknown parents and
    # unfounded cycles are untrusted.
    assert epistemic.unverified_basis(facts) == ["c1", "c2", "f1", "f2", "f5"]


def test_policy_replay_promotes_only_real_improvements():
    events = [{"action": "wire", "new_payee": True, "incident": True},
              {"action": "wire", "new_payee": False, "incident": False},
              {"action": "wire", "new_payee": True, "incident": False}]
    current: list = []
    tight = [{"id": "r1", "action": "wire", "modality": "forbid", "when": {}}]
    smart = [{"id": "r2", "action": "wire", "modality": "forbid", "when": {"new_payee": True}}]
    assert policy.better(tight, current, events)[0] is False           # 2 benign blocks
    ok, report = policy.better(smart, current, events, max_extra_friction=1)
    assert ok and report["candidate"] == {"incidents_stopped": 1, "incidents_missed": 0,
                                          "benign_blocked": 1}


def test_contradictions_need_compatible_conditions():
    rules = [{"id": "gdpr", "action": "delete_record", "modality": "require", "when": {"erasure_request": True}},
             {"id": "hold", "action": "delete_record", "modality": "forbid", "when": {"legal_hold": True}},
             {"id": "tax", "action": "delete_record", "modality": "forbid", "when": {"erasure_request": False}}]
    assert policy.contradictions(rules) == [
        ("hold", "gdpr", {"legal_hold": True, "erasure_request": True})]


def test_learning_envelope_marks_substantial_modifications():
    env = {"temperature": {"min": 0.0, "max": 0.7}, "retriever": {"in": ["bm25", "dense"]}}
    assert not envelope.substantial(env, {"temperature": 0.5, "retriever": "dense"})
    assert envelope.violations(env, {"temperature": 0.9, "retriever": "web", "tools": ["shell"]}) == [
        "retriever: 'web' not among ['bm25', 'dense']",
        "temperature: 0.9 outside [0.0, 0.7]",
        "tools: not in the declared envelope"]


def test_parallelism_flags_a_lagged_follower_only():
    import random
    rng = random.Random(7)
    leader = [100.0]
    for _ in range(40):
        leader.append(leader[-1] + rng.choice([-1, 1]))
    follower = [leader[0]] + leader[:-1]          # copies the leader one period later
    independent = [100.0 + rng.choice([-1, 0, 1]) for _ in leader]
    hit = market.parallelism(leader, follower)
    assert (hit["lag"], hit["co_moves"], hit["flag"]) == (1, 1.0, True)
    assert not market.parallelism(leader, independent)["flag"]


def test_composition_cache_is_per_tenant(monkeypatch):
    from maverick.immune import network, report
    from maverick.paths import tenant_scope
    monkeypatch.setattr(report, "_roster_cache", {})
    built: list = []

    def graph():
        from maverick.paths import current_tenant_id
        built.append(current_tenant_id())
        return network.Graph({f"pack-{current_tenant_id()}": frozenset()})
    monkeypatch.setattr(network, "roster_graph", graph)
    with tenant_scope(tenant="acme"):
        assert report._composition()["agents"] == 1
        report._composition()
    with tenant_scope(tenant="globex"):
        report._composition()
    assert built == ["acme", "globex"]
