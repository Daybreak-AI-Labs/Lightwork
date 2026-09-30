"""Independent checks of signed federation outcome and snapshot boundaries."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest
from maverick.audit import federation as fed
from maverick.audit.signing import AuditSigner, verify_chain


def _log(root, node, events):
    directory = root / node
    directory.mkdir()
    day = dt.datetime.now(dt.timezone.utc).date().isoformat()
    path = directory / f"{day}.ndjson"
    signer = AuditSigner(path)
    for event in events:
        signer.write(event)
    assert verify_chain(path) == []
    return directory, path


def _pair():
    sent = {
        "kind": "federation_delegate", "peer_node": "B",
        "correlation_id": "call-1", "direction": "sent",
        "accepted": True, "remote_goal_id": 7,
    }
    received = {
        "kind": "federation_delegate", "peer_node": "A",
        "correlation_id": "call-1", "direction": "received",
        "accepted": True, "goal_id": 7,
    }
    return sent, received


def _report(root, sent, received):
    a, _ = _log(root, "A", [sent])
    b, _ = _log(root, "B", [received])
    return fed.verify_federation({"A": (a, None), "B": (b, None)})


@pytest.mark.parametrize("value", ["missing", None, 0, 1, "false", []])
def test_native_delegation_cannot_confirm_missing_or_malformed_acceptance(tmp_path, value):
    sent, received = _pair()
    if value == "missing":
        received.pop("accepted")
    else:
        received["accepted"] = value

    report = _report(tmp_path, sent, received)

    assert all(node.intact for node in report.nodes.values())
    assert not report.consistent
    assert report.unresolved_outcomes


@pytest.mark.parametrize("value", [None, 0, True, "7", -1])
def test_native_acceptance_with_invalid_goal_identity_remains_unresolved(tmp_path, value):
    sent, received = _pair()
    received["goal_id"] = value

    report = _report(tmp_path, sent, received)

    assert all(node.intact for node in report.nodes.values())
    assert not report.consistent
    assert report.unresolved_outcomes


@pytest.mark.parametrize("direction", [None, "unknown", "receive-typo"])
def test_native_unknown_direction_does_not_hide_opposing_acceptance(tmp_path, direction):
    sent, received = _pair()
    received["direction"] = direction
    received["accepted"] = False

    report = _report(tmp_path, sent, received)

    assert all(node.intact for node in report.nodes.values())
    assert not report.consistent
    assert report.outcome_conflicts or report.unresolved_outcomes


def test_legacy_generic_reciprocity_does_not_require_delegation_outcomes(tmp_path):
    sent, received = _pair()
    for row in (sent, received):
        row["kind"] = "generic_reference"
        row.pop("accepted")
        row.pop("goal_id", None)
        row.pop("remote_goal_id", None)

    report = _report(tmp_path, sent, received)

    assert report.consistent
    assert not report.unresolved_outcomes
    result = fed.cross_verify(report.nodes)
    assert isinstance(result, tuple) and len(result) == 2
    assert result == ([], [])


def test_native_matching_refusals_do_not_require_goal_identity(tmp_path):
    sent, received = _pair()
    for row in (sent, received):
        row["accepted"] = False
        row.pop("goal_id", None)
        row.pop("remote_goal_id", None)

    report = _report(tmp_path, sent, received)

    assert report.consistent
    assert not report.unresolved_outcomes


def test_snapshot_detects_in_place_change_with_preserved_size_and_mtime(tmp_path, monkeypatch):
    from maverick.audit import signing

    sent, _ = _pair()
    directory, path = _log(tmp_path, "A", [sent])
    original_stat = path.stat()
    original_verify = signing.verify_chain

    def change_after_verify(selected, *args, **kwargs):
        result = original_verify(selected, *args, **kwargs)
        if selected == path:
            original = path.read_bytes()
            replacement = original.replace(b"call-1", b"call-2")
            assert replacement != original and len(replacement) == len(original)
            with path.open("r+b") as stream:
                stream.write(replacement)
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return result

    monkeypatch.setattr(signing, "verify_chain", change_after_verify)
    report = fed.collect_node("A", directory)

    assert not report.intact
    assert any(br.reason == "verify_error" for br in report.breaks)


def test_verified_rows_do_not_depend_on_supplied_row_object_identity(tmp_path):
    sent, _ = _pair()
    directory, path = _log(tmp_path, "A", [sent])
    copied_rows = [json.loads(line) for line in path.read_text().splitlines()]

    report = fed.collect_node("A", directory, copied_rows)

    assert report.intact
    assert report.crossrefs[0].correlation == "call-1"
