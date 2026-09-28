"""Federated audit-log verification: cross-node reciprocity over signed logs."""
from __future__ import annotations

import datetime as dt
import json

import pytest
from maverick.audit import federation as fed


def _sent(node, peer, corr):
    return {"node": node, "peer_node": peer, "correlation_id": corr,
            "direction": "sent", "kind": "delegate"}


def _recv(node, peer, corr):
    return {"node": node, "from_node": peer, "task_id": corr,
            "direction": "received", "kind": "accept"}


def test_extract_crossrefs_needs_peer_and_correlation():
    rows = [
        {"kind": "noise"},                                  # neither
        {"peer_node": "B"},                                 # no correlation
        {"correlation_id": "c1"},                           # no peer
        _sent("A", "B", "c1"),                              # a real reference
    ]
    refs = fed.extract_crossrefs("A", rows)
    assert len(refs) == 1
    assert refs[0].peer == "B" and refs[0].correlation == "c1"
    assert refs[0].direction == "sent"


def test_reciprocated_links_are_consistent():
    nodes = {
        "A": fed.NodeReport("A", True, crossrefs=fed.extract_crossrefs(
            "A", [_sent("A", "B", "c1")])),
        "B": fed.NodeReport("B", True, crossrefs=fed.extract_crossrefs(
            "B", [_recv("B", "A", "c1")])),
    }
    unrecip, untrusted = fed.cross_verify(nodes)
    assert unrecip == [] and untrusted == []


def test_dropped_half_is_unreciprocated():
    # A claims it delegated c1 to B, but B's log has no matching row.
    nodes = {
        "A": fed.NodeReport("A", True, crossrefs=fed.extract_crossrefs(
            "A", [_sent("A", "B", "c1")])),
        "B": fed.NodeReport("B", True, crossrefs=[]),  # B dropped its half
    }
    unrecip, untrusted = fed.cross_verify(nodes)
    assert len(unrecip) == 1
    assert unrecip[0].node == "A" and unrecip[0].peer == "B"
    assert untrusted == []


def test_direction_must_be_opposite():
    # both nodes recorded "sent" for the same correlation -> not a valid pair
    nodes = {
        "A": fed.NodeReport("A", True, crossrefs=fed.extract_crossrefs(
            "A", [_sent("A", "B", "c1")])),
        "B": fed.NodeReport("B", True, crossrefs=fed.extract_crossrefs(
            "B", [_sent("B", "A", "c1")])),
    }
    unrecip, _ = fed.cross_verify(nodes)
    assert len(unrecip) == 2  # neither half pairs with the other


def test_reference_into_broken_peer_is_untrusted():
    nodes = {
        "A": fed.NodeReport("A", True, crossrefs=fed.extract_crossrefs(
            "A", [_sent("A", "B", "c1")])),
        "B": fed.NodeReport("B", False, breaks=["broken"], crossrefs=[]),
    }
    unrecip, untrusted = fed.cross_verify(nodes)
    assert untrusted and untrusted[0].peer == "B"
    assert unrecip == []  # not counted as unreciprocated; peer can't be trusted


def test_reference_to_unknown_node_is_untrusted():
    nodes = {
        "A": fed.NodeReport("A", True, crossrefs=fed.extract_crossrefs(
            "A", [_sent("A", "ghost", "c1")])),
    }
    _unrecip, untrusted = fed.cross_verify(nodes)
    assert untrusted and untrusted[0].peer == "ghost"


def test_verify_federation_missing_audit_dirs_are_broken(tmp_path):
    inputs = {
        "A": (tmp_path / "a", [_sent("A", "B", "c1")]),
        "B": (tmp_path / "b", [_recv("B", "A", "c1")]),
    }
    report = fed.verify_federation(inputs)
    assert report.consistent is False
    assert set(report.nodes) == {"A", "B"}
    assert {br.reason for rep in report.nodes.values() for br in rep.breaks} == {
        "missing_audit_dir"
    }
    out = fed.render(report)
    assert "INCONSISTENT" in out


def test_verify_federation_empty_audit_dirs_are_broken(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    inputs = {
        "A": (a, [_sent("A", "B", "c1")]),
        "B": (b, [_recv("B", "A", "c1")]),
    }
    report = fed.verify_federation(inputs)
    assert report.consistent is False
    assert {br.reason for rep in report.nodes.values() for br in rep.breaks} == {
        "missing_audit_logs"
    }


def test_verify_federation_reports_inconsistency(tmp_path):
    inputs = {
        "A": (tmp_path / "a", [_sent("A", "B", "c1")]),
        "B": (tmp_path / "b", []),  # dropped half
    }
    report = fed.verify_federation(inputs)
    assert report.consistent is False
    # No supplied references are trusted when the files cannot be read.
    assert report.untrusted_peer == []
    assert "INCONSISTENT" in fed.render(report)


def test_collect_node_flags_broken_chain(tmp_path, monkeypatch):
    # A real (signed) day-file that fails verify_chain marks the node broken.
    audit = tmp_path / "node"
    audit.mkdir()
    (audit / "2026-01-01.ndjson").write_text(
        '{"hash": "x", "sig": "y", "prev_hash": ""}\n', encoding="utf-8")
    rep = fed.collect_node("A", audit, [])
    # crypto-less env yields a no_crypto break; either way the node is not intact
    assert rep.intact is False


def _native_log(root, name, events):
    from maverick.audit.signing import AuditSigner, verify_chain

    directory = root / name
    directory.mkdir()
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    path = directory / f"{day}.ndjson"
    signer = AuditSigner(path)
    for event in events:
        signer.write(event)
    assert verify_chain(path) == []
    return directory, path, [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("alteration", ["fabricated", "omitted", "changed_outcome"])
def test_collect_node_binds_supplied_rows_to_signed_native_rows(tmp_path, alteration):
    directory, _path, rows = _native_log(tmp_path, "A", [
        _sent("A", "B", "real") | {"accepted": True, "remote_goal_id": 7},
    ])
    supplied = list(rows)
    if alteration == "fabricated":
        supplied.append(_sent("A", "C", "invented"))
    elif alteration == "omitted":
        supplied = []
    else:
        supplied = [rows[0] | {"accepted": False}]

    report = fed.collect_node("A", directory, supplied)

    assert report.intact is False
    assert "supplied_rows_mismatch" in {br.reason for br in report.breaks}


def test_collect_node_refuses_unsigned_references(tmp_path):
    directory = tmp_path / "A"
    directory.mkdir()
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    row = _sent("A", "B", "unsigned")
    (directory / f"{day}.ndjson").write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = fed.collect_node("A", directory, [row])

    assert report.intact is False
    assert "unsigned" in {br.reason for br in report.breaks}


@pytest.mark.parametrize("sealed", [False, True])
def test_collect_node_detects_replacement_between_verification_and_read(
    tmp_path, monkeypatch, sealed,
):
    from maverick.audit import signing
    from maverick.audit.sealing import seal_closed_segments

    directory, path, rows = _native_log(tmp_path, "A", [_sent("A", "B", "real")])
    if sealed:
        monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
        assert seal_closed_segments(directory, today="2099-12-31")[path.name] == "sealed"
    verify = signing.verify_chain

    def replace_after_verification(selected, *args, **kwargs):
        result = verify(selected, *args, **kwargs)
        if selected == path:
            changed = path.with_suffix(".replacement")
            changed.write_text(json.dumps(_sent("A", "C", "forged")) + "\n")
            changed.replace(path)
        return result

    monkeypatch.setattr(signing, "verify_chain", replace_after_verification)
    report = fed.collect_node("A", directory, rows)

    assert report.intact is False


@pytest.mark.parametrize("sealed", [False, True])
def test_native_federation_reads_and_verifies_the_same_rows(tmp_path, monkeypatch, sealed):
    from maverick.audit.sealing import seal_closed_segments

    a, a_path, a_rows = _native_log(tmp_path, "A", [
        _sent("A", "B", "real") | {"accepted": True, "remote_goal_id": 7},
    ])
    b, b_path, b_rows = _native_log(tmp_path, "B", [
        _recv("B", "A", "real") | {"accepted": True, "goal_id": 7},
    ])
    if sealed:
        monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
        assert seal_closed_segments(a, today="2099-12-31")[a_path.name] == "sealed"
        assert seal_closed_segments(b, today="2099-12-31")[b_path.name] == "sealed"

    report = fed.verify_federation({"A": (a, a_rows), "B": (b, b_rows)})

    assert report.consistent is True
    assert report.nodes["A"].crossrefs[0].correlation == "real"
    assert report.nodes["A"].verification_scope == "selected_segment_snapshots"
    assert report.nodes["A"].anchors_bound_to_rows is False
    assert report.nodes["A"].checkpoints_verified is False


@pytest.mark.parametrize("difference", ["acceptance", "goal"])
def test_native_federation_reports_conflicting_outcomes(tmp_path, difference):
    sent = _sent("A", "B", "real") | {"accepted": True, "remote_goal_id": 7}
    received = _recv("B", "A", "real") | {"accepted": True, "goal_id": 7}
    if difference == "acceptance":
        received["accepted"] = False
    else:
        received["goal_id"] = 8
    a, _a_path, a_rows = _native_log(tmp_path, "A", [sent])
    b, _b_path, b_rows = _native_log(tmp_path, "B", [received])

    report = fed.verify_federation({"A": (a, a_rows), "B": (b, b_rows)})

    assert report.consistent is False
    assert report.outcome_conflicts
    assert "OUTCOME CONFLICT" in fed.render(report)


@pytest.mark.parametrize("state", ["pending", "unknown"])
def test_later_recorded_acceptance_resolves_prior_delivery_uncertainty(tmp_path, state):
    sent = _sent("A", "B", "real") | {
        "kind": "federation_delegate", "accepted": True, "remote_goal_id": 7,
        "outcome_state": "accepted",
    }
    received = _recv("B", "A", "real") | {
        "kind": "federation_delegate", "accepted": True, "goal_id": 7,
    }
    a, _path, rows = _native_log(tmp_path, "A", [
        sent | {"outcome_state": state, "accepted": None, "remote_goal_id": None}, sent,
    ])
    b, _path, received_rows = _native_log(tmp_path, "B", [received])

    report = fed.verify_federation({"A": (a, rows), "B": (b, received_rows)})

    assert report.consistent
    assert not report.unresolved_outcomes


@pytest.mark.parametrize("state", [False, None, [], "unrecognized"])
def test_invalid_explicit_outcome_state_cannot_confirm_delegation(tmp_path, state):
    sent = _sent("A", "B", "real") | {
        "kind": "federation_delegate", "accepted": True, "remote_goal_id": 7,
        "outcome_state": state,
    }
    received = _recv("B", "A", "real") | {
        "kind": "federation_delegate", "accepted": True, "goal_id": 7,
    }
    a, _path, rows = _native_log(tmp_path, "A", [sent])
    b, _path, received_rows = _native_log(tmp_path, "B", [received])

    report = fed.verify_federation({"A": (a, rows), "B": (b, received_rows)})

    assert not report.consistent
    assert report.unresolved_outcomes[0].outcome_issue == "invalid_outcome_state"


def test_render_does_not_upgrade_caller_supplied_evidence():
    report = fed.FederationReport(nodes={"A": fed.NodeReport("A", True)})

    rendered = fed.render(report)

    assert "scope=caller_supplied" in rendered
    assert "anchors_checked=False" in rendered
    assert "Scope: verified segment snapshots" not in rendered


def test_later_acceptance_cannot_resolve_a_different_reserved_goal(tmp_path):
    sent = _sent("A", "B", "real") | {
        "kind": "federation_delegate", "accepted": True, "remote_goal_id": 8,
        "outcome_state": "accepted",
    }
    pending = sent | {"accepted": None, "outcome_state": "pending", "remote_goal_id": 7}
    received = _recv("B", "A", "real") | {
        "kind": "federation_delegate", "accepted": True, "goal_id": 8,
    }
    a, _path, rows = _native_log(tmp_path, "A", [pending, sent])
    b, _path, received_rows = _native_log(tmp_path, "B", [received])

    report = fed.verify_federation({"A": (a, rows), "B": (b, received_rows)})

    assert not report.consistent
    assert report.unresolved_outcomes[0].remote_goal_id == 7
