"""Policy retention must not be indistinguishable from tampering.

Three defects shared one theme: the audit chain treated a *legitimate*
compliance operation as evidence of attack, or destroyed evidence while
reporting success.

* A ``[retention] audit_days`` purge deletes old day-files. Their anchors stay
  in the append-only ledger by design, so ``verify_anchors`` reported
  ``anchored_file_deleted`` for every purged day -- permanently, since the files
  are gone. A retention-enabled deployment could never get a green
  ``audit verify``, which trains operators to ignore the one check that would
  show real tampering.
* The same missing-file condition made ``audit worm push`` raise
  ``WormUnavailable`` forever, because the anchor-ledger reader enforced exact
  key-set equality against the anchor shape.
* A GDPR erase is an unlocked read-modify-write racing the live signer. Rows
  committed between the read and the atomic replace were destroyed, and the
  re-anchor then re-signed the truncated file so ``verify_chain`` reported
  CLEAN. Measured at 3-4 rows per run under a modest concurrent writer.

The risk in fixing the first two is obvious and is what most of this file
tests: an excusal mechanism must not become a laundering mechanism. A signed
``retention_purge`` row commits to the exact tip it destroyed, must post-date
the anchor it retires, and is honoured only inside an intact ledger chain.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

crypto = pytest.importorskip(
    "cryptography", reason="audit-signing extra not installed")

from maverick.audit import erase, retention  # noqa: E402
from maverick.audit.signing import (  # noqa: E402
    ANCHOR_FILENAME,
    AuditSigner,
    ensure_anchors,
    record_retention_purge,
    verify_anchors,
    verify_chain,
)


def _day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=offset)).isoformat()


def _write_day(audit_dir: Path, day: str, rows: int = 3, **extra) -> Path:
    path = audit_dir / f"{day}.ndjson"
    signer = AuditSigner(path)
    for i in range(rows):
        assert signer.write({
            "ts": time.time(), "kind": "tool_call", "agent": "a",
            "goal_id": None, "payload": {"i": i}, **extra,
        })
    return path


@pytest.fixture
def audit_dir(tmp_path, monkeypatch) -> Path:
    # MAVERICK_HOME is set so this dir IS what data_dir("audit") resolves to,
    # letting the CLI test below exercise the real resolution path rather than
    # a hand-passed directory.
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    d = tmp_path / "audit"
    d.mkdir()
    from maverick.paths import data_dir
    assert data_dir("audit") == d, data_dir("audit")
    return d


@pytest.fixture
def purged(audit_dir) -> Path:
    """An audit dir with two anchored days legitimately purged by policy."""
    for day in (_day(40), _day(39), _day(1)):
        _write_day(audit_dir, day)
    assert ensure_anchors(audit_dir) == 3
    assert verify_anchors(audit_dir) == [], "precondition: clean before purge"
    report = retention.enforce(config={"audit_days": 30}, audit_dir=audit_dir)
    assert len(report["audit"]["removed"]) == 2, report
    return audit_dir


# -- the bug ---------------------------------------------------------------

def test_a_policy_purge_leaves_audit_verify_clean(purged) -> None:
    assert verify_anchors(purged) == []


def test_the_purge_is_recorded_in_the_ledger_not_only_a_day_file(purged) -> None:
    """The record must outlive the next purge.

    A marker written into a live day-file is itself deleted ~30 days later and
    the excusal evaporates -- the break returns with nothing left to explain it.
    Retention prunes only ``YYYY-MM-DD.ndjson``, so the ledger is the durable
    home. This asserts placement, not merely existence.
    """
    rows = [
        json.loads(ln)
        for ln in (purged / ANCHOR_FILENAME).read_text().splitlines() if ln.strip()
    ]
    marks = [r for r in rows if r.get("kind") == "retention_purge"]
    assert len(marks) == 1, rows
    assert {e["day"] for e in marks[0]["days"]} == {_day(40), _day(39)}
    # Every entry commits to what it destroyed, so an archived copy can be
    # reconciled against the claim.
    for entry in marks[0]["days"]:
        assert len(entry["tip_hash"]) == 64
        assert entry["row_count"] == 3


def test_the_purge_record_is_itself_signed_and_chained(purged) -> None:
    assert verify_chain(purged / ANCHOR_FILENAME) == []


# -- negative controls: the excusal must discriminate -----------------------

def test_an_unrecorded_deletion_still_fails(audit_dir) -> None:
    """Positive control. Without this the tests above prove only permissiveness."""
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    (audit_dir / f"{_day(40)}.ndjson").unlink()

    breaks = verify_anchors(audit_dir)
    assert [b.reason for b in breaks] == ["anchored_file_deleted"], breaks


def test_a_purge_record_naming_the_wrong_tip_does_not_excuse(audit_dir) -> None:
    """Tamper-then-purge must not launder.

    An attacker who edits a day-file and then deletes it would otherwise get a
    clean verify by claiming retention. The record commits to a tip, so a file
    whose content diverged from its last anchor cannot be excused.
    """
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    (audit_dir / f"{_day(40)}.ndjson").unlink()
    record_retention_purge(
        audit_dir, [{"day": _day(40), "tip_hash": "b" * 64, "row_count": 3}])

    breaks = verify_anchors(audit_dir)
    assert [b.reason for b in breaks] == ["anchored_file_deleted"], breaks


def test_a_purge_record_naming_the_wrong_row_count_does_not_excuse(
    audit_dir,
) -> None:
    """Truncate-then-purge: the tip can be genuine while rows are missing."""
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    anchor = [
        json.loads(ln)
        for ln in (audit_dir / ANCHOR_FILENAME).read_text().splitlines()
        if ln.strip() and json.loads(ln).get("day") == _day(40)
    ][0]
    (audit_dir / f"{_day(40)}.ndjson").unlink()
    record_retention_purge(
        audit_dir,
        [{"day": _day(40), "tip_hash": anchor["tip_hash"], "row_count": 99}])

    assert [b.reason for b in verify_anchors(audit_dir)] == ["anchored_file_deleted"]


def test_a_purge_record_cannot_pre_authorize_a_future_deletion(audit_dir) -> None:
    """The ledger is append-only, so ordering is meaningful evidence.

    A purge row that predates the anchor it retires cannot have been written by
    a retention run that observed that anchor's file.
    """
    _write_day(audit_dir, _day(1))       # gives the ledger a row to exist for
    _write_day(audit_dir, _day(2))
    ensure_anchors(audit_dir)
    # Pre-authorize a day that is not anchored yet.
    future = _day(40)
    path = _write_day(audit_dir, future)
    from maverick.audit.signing import _file_tip_and_count
    tip, count = _file_tip_and_count(path)
    record_retention_purge(
        audit_dir, [{"day": future, "tip_hash": tip, "row_count": count}])
    # ...then anchor it and delete it.
    ensure_anchors(audit_dir)
    path.unlink()

    assert [b.reason for b in verify_anchors(audit_dir)] == ["anchored_file_deleted"]


def test_a_forged_purge_row_in_a_broken_ledger_does_not_excuse(audit_dir) -> None:
    """Fail closed: an unsigned append must not become a permission slip.

    Appending a plausible-looking purge row costs nothing without the signing
    key, so the excusal is honoured only when the ledger's own chain verifies.
    """
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    anchor = [
        json.loads(ln)
        for ln in (audit_dir / ANCHOR_FILENAME).read_text().splitlines()
        if ln.strip() and json.loads(ln).get("day") == _day(40)
    ][0]
    (audit_dir / f"{_day(40)}.ndjson").unlink()
    with open(audit_dir / ANCHOR_FILENAME, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "kind": "retention_purge",
            "days": [{"day": _day(40), "tip_hash": anchor["tip_hash"],
                      "row_count": anchor["row_count"]}],
            "cutoff_day": _day(40),
            "ts": datetime.now(timezone.utc).isoformat(),
        }) + "\n")

    reasons = {b.reason for b in verify_anchors(audit_dir)}
    assert "anchored_file_deleted" in reasons, reasons


def test_a_purge_record_does_not_excuse_a_different_day(audit_dir) -> None:
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(39))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    from maverick.audit.signing import _file_tip_and_count
    tip, count = _file_tip_and_count(audit_dir / f"{_day(40)}.ndjson")
    (audit_dir / f"{_day(40)}.ndjson").unlink()
    (audit_dir / f"{_day(39)}.ndjson").unlink()
    record_retention_purge(
        audit_dir, [{"day": _day(40), "tip_hash": tip, "row_count": count}])

    breaks = verify_anchors(audit_dir)
    assert len(breaks) == 1, breaks
    assert _day(39) in breaks[0].detail


def test_an_unreadable_day_is_still_deleted_but_warns_and_is_not_excused(
    audit_dir, caplog,
) -> None:
    """Storage limitation wins over attestability, but not silently.

    A sealed segment whose at-rest key is unavailable cannot be read, so the
    purge cannot commit to a tip. Deleting it is still the legal obligation;
    excusing it in verify would be a hole big enough to drive an attack through.
    """
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    # Scope the unreadability to the purge itself. Left in place it would also
    # break verify's read of the SURVIVING day, and monkeypatch.undo() would
    # take the fixture's MAVERICK_HOME with it (moving the key dir, so verify
    # reports no_pubkey instead of what is under test).
    from maverick.audit import signing as _signing
    original = _signing._file_tip_and_count
    _signing._file_tip_and_count = lambda p: (_ for _ in ()).throw(
        RuntimeError("sealed: key unavailable"))
    try:
        with caplog.at_level("WARNING"):
            retention.enforce(config={"audit_days": 30}, audit_dir=audit_dir)
    finally:
        _signing._file_tip_and_count = original

    assert not (audit_dir / f"{_day(40)}.ndjson").exists(), "must still delete"
    assert any("cannot be attested" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
    assert [b.reason for b in verify_anchors(audit_dir)] == ["anchored_file_deleted"]


def test_a_purge_record_without_crypto_is_not_written(audit_dir, monkeypatch) -> None:
    """Anti-vacuity for the helper: no silent success when it cannot sign."""
    monkeypatch.setattr("maverick.audit.signing._have_crypto", lambda: False)
    assert record_retention_purge(
        audit_dir, [{"day": _day(40), "tip_hash": "a" * 64, "row_count": 1}]) is False


def test_a_purge_of_nothing_writes_no_record(audit_dir) -> None:
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    before = (audit_dir / ANCHOR_FILENAME).read_text() \
        if (audit_dir / ANCHOR_FILENAME).exists() else ""
    retention.enforce(config={"audit_days": 3650}, audit_dir=audit_dir)
    after = (audit_dir / ANCHOR_FILENAME).read_text() \
        if (audit_dir / ANCHOR_FILENAME).exists() else ""
    assert before == after


def test_audit_verify_states_the_deletions_it_stopped_reporting(purged) -> None:
    """A silent skip would make the check less informative than the bug."""
    from click.testing import CliRunner
    from maverick.cli import main

    result = CliRunner().invoke(main, ["audit", "verify", "--all"])
    out = result.output
    assert "retired by [retention] policy" in out, out
    assert _day(40) in out and _day(39) in out, out


def test_the_purged_day_listing_is_empty_without_a_purge(audit_dir) -> None:
    """Negative control: the note must not appear on an untouched chain."""
    from maverick.audit.signing import retention_purged_days
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    assert retention_purged_days(audit_dir) == []


def test_a_purge_leaves_no_orphaned_lock_sidecar(purged) -> None:
    """Retention exists to bound growth; it must not leak one inode per day."""
    leftovers = sorted(
        p.name for p in purged.iterdir()
        if p.name.endswith(".lock") and p.name.startswith(_day(40)[:7])
    )
    assert leftovers == [], leftovers


def test_a_dry_run_purge_writes_no_record(audit_dir) -> None:
    _write_day(audit_dir, _day(40))
    _write_day(audit_dir, _day(1))
    ensure_anchors(audit_dir)
    before = (audit_dir / ANCHOR_FILENAME).read_text()
    retention.enforce(config={"audit_days": 30}, audit_dir=audit_dir, dry_run=True)
    assert (audit_dir / ANCHOR_FILENAME).read_text() == before
    assert (audit_dir / f"{_day(40)}.ndjson").exists()


# -- WORM push must survive a legitimate purge -----------------------------

def _closed_dayfiles(audit_dir: Path) -> list[Path]:
    """Exactly what ``push_closed_dayfiles`` ships: completed days only.

    Today's file is still growing and is deliberately never anchored, so it is
    not part of the snapshot. Retention's own live-chain marker creates it.
    """
    from maverick.audit.signing import day_files
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [p for p in day_files(audit_dir) if p.stem < today]

def test_worm_push_reads_a_ledger_containing_purge_rows(purged) -> None:
    """The reader enforced exact anchor key-set equality on every row.

    A ``retention_purge`` row therefore made the whole ledger 'invalid' and
    every subsequent push raised. It must instead validate that row under its
    own schema and drop the purged day from the set it expects to read.
    """
    from maverick.audit.worm import _verified_source_snapshot

    closed = _closed_dayfiles(purged)
    snapshot = _verified_source_snapshot(purged, closed)
    assert set(snapshot) == {p.name for p in closed}


def test_worm_push_still_rejects_a_malformed_purge_row(purged) -> None:
    """Negative control for the schema branch just added."""
    from maverick.audit.worm import WormUnavailable, _verified_source_snapshot

    signer = AuditSigner(purged / ANCHOR_FILENAME)
    signer.write({"kind": "retention_purge", "days": "not-a-list",
                  "cutoff_day": "", "ts": "x"})
    closed = _closed_dayfiles(purged)
    with pytest.raises(WormUnavailable):
        _verified_source_snapshot(purged, closed)


def test_worm_push_still_rejects_a_malformed_anchor_row(purged) -> None:
    from maverick.audit.worm import WormUnavailable, _verified_source_snapshot

    signer = AuditSigner(purged / ANCHOR_FILENAME)
    signer.write({"kind": "anchor", "day": _day(1), "tip_hash": "nope",
                  "row_count": 1, "ts": "x"})
    closed = _closed_dayfiles(purged)
    with pytest.raises(WormUnavailable):
        _verified_source_snapshot(purged, closed)


# -- erase must not destroy concurrent rows --------------------------------

def _concurrent_erase(audit_dir: Path, *, delete: bool) -> tuple[int, int]:
    """Run an erase while a second signer appends. Returns (committed, present)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = audit_dir / f"{today}.ndjson"
    signer = AuditSigner(path)
    for i in range(200):
        signer.write({"ts": time.time(), "kind": "tool_call", "agent": "a",
                      "goal_id": None, "channel": "slack", "user_id": "victim",
                      "payload": {"i": i}})

    stop = threading.Event()
    committed = [0]
    failures: list[BaseException] = []

    def writer() -> None:
        w = AuditSigner(path)
        try:
            while not stop.is_set():
                if w.write({"ts": time.time(), "kind": "tool_call",
                            "agent": "concurrent", "goal_id": None,
                            "payload": {"n": committed[0]}}):
                    committed[0] += 1
                time.sleep(0.0005)
        except BaseException as exc:  # pragma: no cover - surfaced below
            failures.append(exc)

    t = threading.Thread(target=writer)
    t.start()
    try:
        time.sleep(0.05)
        if delete:
            erase.delete_user("slack", "victim", audit_dir=audit_dir)
        else:
            erase.scrub_user("slack", "victim", audit_dir=audit_dir)
        time.sleep(0.05)
    finally:
        stop.set()
        t.join(timeout=10)
    assert not failures, failures

    present = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("agent") == "concurrent":
                present += 1
        except json.JSONDecodeError:
            pass
    return committed[0], present


def test_a_scrub_destroys_no_concurrently_committed_row(audit_dir) -> None:
    committed, present = _concurrent_erase(audit_dir, delete=False)
    assert committed > 5, f"anti-vacuity: only {committed} concurrent writes raced"
    assert present == committed, f"lost {committed - present} committed audit rows"


def test_a_delete_destroys_no_concurrently_committed_row(audit_dir) -> None:
    committed, present = _concurrent_erase(audit_dir, delete=True)
    assert committed > 5, f"anti-vacuity: only {committed} concurrent writes raced"
    assert present == committed, f"lost {committed - present} committed audit rows"


def test_the_chain_still_verifies_after_a_concurrent_erase(audit_dir) -> None:
    """Zero loss is only half of it: the surviving chain must be intact.

    The old code produced a chain that verified clean *because* it had been
    re-signed over the truncated content, so "verify_chain is empty" alone was
    never evidence. Paired with the row-count assertions above, it is.
    """
    _concurrent_erase(audit_dir, delete=False)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert verify_chain(audit_dir / f"{today}.ndjson") == []


def test_erase_still_refuses_a_file_whose_chain_is_already_broken(
    audit_dir,
) -> None:
    """Negative control: locking must not have made the erase less strict."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = _write_day(audit_dir, today, rows=4,
                      channel="slack", user_id="victim")
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["payload"] = {"tampered": True}
    lines[1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    matched, _ = erase.scrub_user("slack", "victim", audit_dir=audit_dir)
    assert matched == 0, "an erase must not rewrite an already-broken chain"
