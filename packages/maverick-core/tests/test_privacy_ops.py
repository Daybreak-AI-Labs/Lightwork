"""Privacy ops record types: DPA review, AI registry, RoPA, DSAR tracker."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from maverick import privacy_ops


@pytest.fixture(autouse=True)
def _fresh_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))


GOOD_DPA = """
Data Processing Agreement between Controller and Acme Corp.
The processor shall process personal data only on documented instructions
from the controller. All personnel are bound by confidentiality obligations.
The processor implements the technical and organisational measures of
Article 32, including encryption of data at rest. Sub-processors require
prior written authorization and flow-down of these terms. The processor
shall assist the controller with data subject requests. The processor shall
notify the controller of any personal data breach without undue delay.
Upon termination the processor shall delete or return all personal data.
The controller has audit and inspection rights. International transfers are
covered by Standard Contractual Clauses. Data is retained for 12 months
(retention schedule, Annex 2).
"""

BAD_DPA = """
Master agreement. The vendor provides marketing analytics over customer
personal data, including transfer to processing centers outside the EU.
Payment terms are net 30. Either party may terminate with notice.
"""


class TestDpaReview:
    def test_complete_dpa_scores_low_residual(self):
        r = privacy_ops.review_dpa("Acme Corp", GOOD_DPA,
                                   document_name="acme-dpa.pdf")
        assert r["clauses_total"] == len(privacy_ops.DPA_CHECKLIST)
        assert r["clauses_present"] >= 9
        assert r["residual_risk"] in ("minimal", "low")
        # Transfers are declared, so inherent exposure stays high even
        # though the safeguards clause is present.
        assert r["inherent_risk"] == "high"
        by_key = {c["key"]: c for c in r["clauses"]}
        assert by_key["security"]["status"] == "present"
        assert by_key["security"]["excerpt"], "verdicts carry evidence"

    def test_bare_contract_scores_high_residual_with_missing_clauses(self):
        r = privacy_ops.review_dpa("SketchyVendor", BAD_DPA)
        assert r["residual_risk"] == "high"
        by_key = {c["key"]: c for c in r["clauses"]}
        assert by_key["instructions"]["status"] == "missing"
        assert by_key["breach"]["status"] == "missing"
        assert "international transfers" in r["exposures"]

    def test_round_trip_and_summaries(self):
        r = privacy_ops.review_dpa("Acme Corp", GOOD_DPA)
        assert privacy_ops.get_dpa_review(r["id"])["vendor"] == "Acme Corp"
        rows = privacy_ops.list_dpa_reviews()
        assert rows[0]["id"] == r["id"]
        assert "clauses" not in rows[0], "summaries stay light"
        assert privacy_ops.get_dpa_review("../../etc/passwd") is None

    def test_long_actor_labels_remain_collision_resistant(self):
        common = "user:" + ("a" * 300)
        first = privacy_ops.review_dpa(
            "One", GOOD_DPA, reviewed_by=common + "-one",
        )
        second = privacy_ops.review_dpa(
            "Two", GOOD_DPA, reviewed_by=common + "-two",
        )
        assert len(first["reviewed_by"]) <= 256
        assert len(second["reviewed_by"]) <= 256
        assert first["reviewed_by"] != second["reviewed_by"]
        assert "#sha256:" in first["reviewed_by"]


class TestAiRegistry:
    def test_high_risk_classification_with_signals(self):
        r = privacy_ops.register_ai_system(
            "CV screener", "Ranks job candidates by resume for hiring")
        assert r["tier"] == "high"
        assert any("employment" in s for s in r["signals"])
        assert any("Art. 43" in o for o in r["obligations"])

    def test_prohibited_and_limited_and_minimal(self):
        assert privacy_ops.classify_ai_system(
            "CitizenRank", "social scoring of residents")["tier"] == "prohibited"
        assert privacy_ops.classify_ai_system(
            "HelpBot", "customer service chatbot")["tier"] == "limited"
        assert privacy_ops.classify_ai_system(
            "SpamFilter", "filters junk mail internally")["tier"] == "minimal"

    def test_registry_round_trip(self):
        r = privacy_ops.register_ai_system("HelpBot", "support chatbot",
                                           provider="Anthropic", owner="cx")
        assert privacy_ops.get_ai_system(r["id"])["provider"] == "Anthropic"
        assert privacy_ops.list_ai_systems()[0]["id"] == r["id"]


class TestRopa:
    def test_upsert_and_export_shape(self):
        r = privacy_ops.upsert_ropa({
            "activity": "Email marketing", "purpose": "Campaigns",
            "data_categories": "Contact data", "retention": "24 months"})
        rid = r["id"]
        privacy_ops.upsert_ropa(
            {"retention": "12 months"},
            ropa_id=rid,
            expected_revision=r["revision"],
        )
        rows = privacy_ops.export_ropa_art30()
        row = next(x for x in rows if x["id"] == rid)
        assert row["retention"] == "12 months"
        assert row["activity"] == "Email marketing"   # untouched fields kept
        assert set(privacy_ops.ROPA_FIELDS) <= set(row)

    def test_draft_from_assessment_maps_answers(self):
        from maverick.assessment import AssessmentSession, save_session
        s = AssessmentSession(type="pia", subject="Acme CRM")
        s.record("pia_transfers", "yes", "us-east-1, no SCCs")
        s.record("pia_retention", "no")
        s.record("pia_security", "yes")
        s.record("pia_processors", "no")
        save_session(s)
        r = privacy_ops.draft_ropa_from_assessment(s.id)
        assert r is not None
        assert "Acme CRM" in r["activity"]
        assert "WITHOUT Chapter V safeguard" in r["transfers"]
        assert "No defined retention" in r["retention"]
        assert "without countersigned DPA" in r["recipients"]
        assert r["assessment_id"] == s.id

    def test_draft_from_unknown_assessment_is_none(self):
        assert privacy_ops.draft_ropa_from_assessment("nope") is None

    def test_atomic_partial_updates_and_stale_revision_cas(self, monkeypatch):
        import maverick.audit as audit

        monkeypatch.setattr(audit, "record", lambda *a, **k: True)
        created = privacy_ops.upsert_ropa({
            "activity": "Payroll", "purpose": "Pay employees",
        })
        assert created is not None
        rid = created["id"]
        initial_revision = created["revision"]

        updates = [
            ("retention", {"retention": "7 years"}),
            ("controller", {"controller": "Acme Ltd"}),
        ]

        def attempt(item):
            label, payload = item
            try:
                return label, privacy_ops.upsert_ropa(
                    payload,
                    ropa_id=rid,
                    expected_revision=initial_revision,
                )
            except privacy_ops.RecordConflict:
                return label, None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, updates))

        assert sum(record is not None for _, record in outcomes) == 1

        final = next(r for r in privacy_ops.list_ropa() if r["id"] == rid)
        assert final["revision"] == initial_revision + 1
        losing_label = next(label for label, record in outcomes if record is None)
        losing_payload = dict(updates)[losing_label]
        retried = privacy_ops.upsert_ropa(
            losing_payload,
            ropa_id=rid,
            expected_revision=final["revision"],
        )
        assert retried is not None
        assert retried["retention"] == "7 years"
        assert retried["controller"] == "Acme Ltd"
        assert retried["revision"] == initial_revision + 2
        with pytest.raises(ValueError, match="revision is required"):
            privacy_ops.upsert_ropa(
                {"purpose": "unversioned overwrite"},
                ropa_id=rid,
            )
        with pytest.raises(privacy_ops.RecordConflict):
            privacy_ops.upsert_ropa(
                {"purpose": "stale overwrite"},
                ropa_id=rid,
                expected_revision=initial_revision,
            )


class TestDsar:
    def test_open_sets_statutory_due_date(self):
        r = privacy_ops.open_dsar("jordan@example.com", "access",
                                  channel="email")
        assert r["status"] == "open"
        listed = privacy_ops.list_dsars()[0]
        assert 28 <= listed["days_left"] <= 30
        assert listed["overdue"] is False
        with pytest.raises(ValueError):
            privacy_ops.open_dsar("x", "espionage")

    def test_access_fulfillment_runs_real_export(self, tmp_path):
        r = privacy_ops.open_dsar("jordan@example.com", "access",
                                  channel="email")
        done = privacy_ops.fulfill_dsar(r["id"])
        assert done["status"] == "fulfilled"
        path = done["fulfillment"]["export_path"]
        bundle = json.loads(open(path, encoding="utf-8").read())
        assert bundle["subject"]["user_id"] == "jordan@example.com"
        assert "counts" in done["fulfillment"]
        # The bundle next door must not be mistaken for a request record
        # (regression: the store globs *.json, exports live in a subdir).
        listed = privacy_ops.list_dsars()
        assert [x["status"] for x in listed if x["id"] == r["id"]] == ["fulfilled"]

    def test_erasure_fulfillment_is_structured_not_shell_text(self):
        subject = "jordan@example.com & calc.exe | whoami > owned.txt"
        channel = "email & echo %COMSPEC% ^ !PATH!"
        r = privacy_ops.open_dsar(subject, "erasure", channel=channel)
        ready = privacy_ops.fulfill_dsar(r["id"])
        assert ready["status"] == "awaiting_erasure"
        handoff = ready["fulfillment"]
        assert handoff["erase_argv"] == [
            "maverick", "erase", "--user", subject, "--channel", channel,
        ]
        assert "erase_command" not in handoff
        assert subject not in handoff["operator_instruction"]
        assert "shell command" in handoff["operator_instruction"]
        closed = privacy_ops.close_dsar(r["id"], closed_by="privacy-lead")
        assert closed["status"] == "closed"

    def test_concurrent_fulfillment_exports_exactly_once(self, monkeypatch):
        import maverick.audit as audit
        from maverick import dsar

        monkeypatch.setattr(audit, "record", lambda *a, **k: True)
        calls = 0
        calls_lock = threading.Lock()

        def fake_export(user_id, *, channel=None, tenant=None):
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.03)
            return {
                "subject": {"user_id": user_id, "channel": channel},
                "tenant": tenant,
                "counts": {"facts": 1},
            }

        monkeypatch.setattr(dsar, "export_subject_data", fake_export)
        opened = privacy_ops.open_dsar(
            "jordan@example.com", "access", channel="email",
            opened_by="user:alice",
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda _: privacy_ops.fulfill_dsar(
                    opened["id"], fulfilled_by="user:alice",
                ),
                range(8),
            ))

        assert calls == 1
        assert all(result is not None for result in results)
        assert {r["status"] for r in results if r is not None} == {"fulfilled"}
        assert len({r["fulfillment"]["export_path"] for r in results
                    if r is not None}) == 1
        stored = privacy_ops.list_dsars()[0]
        assert stored["revision"] == opened["revision"] + 1
        assert "_audit_pending" not in stored

    def test_planted_or_cross_subject_export_is_regenerated(self, monkeypatch):
        import maverick.audit as audit
        from maverick import dsar
        from maverick.file_lock import atomic_write_text, ensure_private_directory
        from maverick.paths import data_dir

        monkeypatch.setattr(audit, "record", lambda *a, **k: True)
        calls = []

        def fake_export(user_id, *, channel=None, tenant=None):
            calls.append((user_id, channel, tenant))
            return {
                "subject": {"user_id": user_id, "channel": channel},
                "tenant": tenant,
                "counts": {"facts": 1},
            }

        monkeypatch.setattr(dsar, "export_subject_data", fake_export)
        first = privacy_ops.open_dsar("alice", "access", channel="email")
        out_dir = ensure_private_directory(data_dir("dsar_requests") / "exports")
        first_path = out_dir / f"{first['id']}-export.json"
        atomic_write_text(first_path, "{}", mode=0o600)
        first_done = privacy_ops.fulfill_dsar(first["id"])
        assert first_done is not None and first_done["status"] == "fulfilled"
        assert calls == [("alice", "email", None)]

        second = privacy_ops.open_dsar("bob", "access", channel="teams")
        second_path = out_dir / f"{second['id']}-export.json"
        atomic_write_text(
            second_path,
            first_path.read_text(encoding="utf-8"),
            mode=0o600,
        )
        second_done = privacy_ops.fulfill_dsar(second["id"])
        assert second_done is not None and second_done["status"] == "fulfilled"
        assert calls[-1] == ("bob", "teams", None)
        artifact = json.loads(second_path.read_text(encoding="utf-8"))
        assert artifact["subject"] == {"user_id": "bob", "channel": "teams"}
        assert artifact["_lightwork_export"]["intent"]["request_id"] == second["id"]
        assert len(second_done["fulfillment"]["artifact_sha256"]) == 64

    def test_crash_after_artifact_write_reuses_bound_export(self, monkeypatch):
        import maverick.audit as audit
        from maverick import dsar

        monkeypatch.setattr(audit, "record", lambda *a, **k: True)
        calls = 0

        def fake_export(user_id, *, channel=None, tenant=None):
            nonlocal calls
            calls += 1
            return {
                "subject": {"user_id": user_id, "channel": channel},
                "tenant": tenant,
                "counts": {"facts": 1},
            }

        monkeypatch.setattr(dsar, "export_subject_data", fake_export)
        opened = privacy_ops.open_dsar("alice", "access", channel="email")
        original_write = privacy_ops._RecordStore._write_path

        def crash_after_artifact(
            cls, path, record, previous, *, bump_revision=True,
        ):
            if path.stem == opened["id"] and record.get("status") == "fulfilled":
                raise OSError("simulated crash before record commit")
            return original_write(
                path,
                record,
                previous,
                bump_revision=bump_revision,
            )

        with monkeypatch.context() as crash:
            crash.setattr(
                privacy_ops._RecordStore,
                "_write_path",
                classmethod(crash_after_artifact),
            )
            with pytest.raises(OSError, match="simulated crash"):
                privacy_ops.fulfill_dsar(opened["id"])

        assert calls == 1
        assert privacy_ops._DSAR.load(opened["id"])["status"] == "open"
        recovered = privacy_ops.fulfill_dsar(opened["id"])
        assert recovered is not None and recovered["status"] == "fulfilled"
        assert calls == 1
        assert Path(recovered["fulfillment"]["export_path"]).is_file()


def test_audit_outbox_retries_without_revision_churn(monkeypatch):
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *a, **k: False)
    created = privacy_ops.review_dpa(
        "Acme", GOOD_DPA, reviewed_by="user:alice",
    )
    revision = created["revision"]
    pending = created.get("_audit_pending")
    assert isinstance(pending, list) and len(pending) == 1
    event_id = pending[0]["event_id"]

    captured = []

    def accept(kind, **payload):
        captured.append((kind, payload))
        return True

    monkeypatch.setattr(audit, "record", accept)
    assert privacy_ops.retry_pending_audits() == 1
    stored = privacy_ops.get_dpa_review(created["id"])
    assert stored is not None
    assert stored["revision"] == revision
    assert "_audit_pending" not in stored
    assert captured[0][1]["event_id"] == event_id
    assert captured[0][1]["revision"] == revision
    assert captured[0][1]["status"] == "pending_review"
    assert len(captured[0][1]["record_sha256"]) == 64
    assert captured[0][1]["occurred_at"] > 0
    assert privacy_ops.retry_pending_audits() == 0
    assert privacy_ops.get_dpa_review(created["id"])["revision"] == revision


def test_privacy_store_refuses_linked_root(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    root = tmp_path / "dpa_reviews"
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    with pytest.raises(PermissionError, match="aliases"):
        privacy_ops.review_dpa("Acme", GOOD_DPA)
    assert list(outside.iterdir()) == []


def test_privacy_store_refuses_hardlink_and_mismatched_identity(tmp_path):
    from maverick.file_lock import atomic_write_text

    created = privacy_ops.review_dpa("Acme", GOOD_DPA)
    root = tmp_path / "dpa_reviews"
    source = root / f"{created['id']}.json"
    linked = root / "DPA-deadbeef00.json"
    try:
        linked.hardlink_to(source)
    except OSError:
        pytest.skip("hardlinks are unavailable on this host")
    with pytest.raises(PermissionError, match="single-link"):
        privacy_ops._DPA.load(created["id"])
    linked.unlink()

    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["id"] = "DPA-deadbeef00"
    atomic_write_text(source, json.dumps(payload), mode=0o600)
    with pytest.raises(PermissionError, match="identity"):
        privacy_ops._DPA.load(created["id"])


def test_privacy_store_rejects_malformed_filename(tmp_path):
    from maverick.file_lock import atomic_write_text, ensure_private_directory

    root = ensure_private_directory(tmp_path / "dsar_requests")
    malformed = root / "DSAR-attacker!.json"
    atomic_write_text(
        malformed,
        json.dumps({"id": "DSAR-attacker!", "status": "open"}),
        mode=0o600,
    )
    with pytest.raises(PermissionError, match="identity"):
        privacy_ops._DSAR.list()


def test_privacy_handle_read_does_not_follow_swap_to_secret(tmp_path, monkeypatch):
    created = privacy_ops.review_dpa("Acme", GOOD_DPA)
    record_path = tmp_path / "dpa_reviews" / f"{created['id']}.json"
    secret = tmp_path / "outside-secret.json"
    secret.write_text(
        json.dumps({"id": created["id"], "vendor": "DO NOT DISCLOSE"}),
        encoding="utf-8",
    )
    secret.chmod(0o600)
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(secret)
        probe.unlink()
    except OSError:
        pytest.skip("symlinks are unavailable on this host")

    real_open = privacy_ops.os.open
    swapped = False

    def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        candidate = Path(path)
        if not swapped and candidate.name == record_path.name:
            swapped = True
            record_path.unlink()
            record_path.symlink_to(secret)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(privacy_ops.os, "open", swapping_open)
    try:
        result = privacy_ops._DPA.load(created["id"])
    except PermissionError:
        # Windows exposes the path/handle mismatch directly; POSIX O_NOFOLLOW
        # may instead be normalized to an unreadable record. Both are a
        # fail-closed outcome, provided the outside payload is never returned.
        result = None
    assert result is None or result.get("vendor") != "DO NOT DISCLOSE"
    assert swapped is True


def test_audit_outbox_is_at_least_once_with_stable_event_id(monkeypatch):
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *a, **k: False)
    created = privacy_ops.review_dpa("Acme", GOOD_DPA, reviewed_by="user:alice")
    event_id = created["_audit_pending"][0]["event_id"]
    captured = []
    monkeypatch.setattr(
        audit,
        "record",
        lambda kind, **payload: captured.append(payload["event_id"]) or True,
    )
    original_write = privacy_ops._RecordStore._write_path

    def crash_before_clear(cls, path, record, previous, *, bump_revision=True):
        if not bump_revision:
            raise OSError("simulated crash after signed append")
        return original_write(
            path, record, previous, bump_revision=bump_revision,
        )

    with monkeypatch.context() as crash:
        crash.setattr(
            privacy_ops._RecordStore,
            "_write_path",
            classmethod(crash_before_clear),
        )
        with pytest.raises(OSError, match="simulated crash"):
            privacy_ops.retry_pending_audits()
    assert privacy_ops.retry_pending_audits() == 1
    assert captured == [event_id, event_id]


def test_audit_retry_limit_bounds_failed_attempts(monkeypatch):
    import maverick.audit as audit

    calls = 0

    def unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(audit, "record", unavailable)
    for index in range(5):
        privacy_ops.review_dpa(f"Vendor {index}", GOOD_DPA)
    original_read = privacy_ops._DPA._read_path
    reads = 0

    def counted_read(path):
        nonlocal reads
        reads += 1
        return original_read(path)

    monkeypatch.setattr(privacy_ops._DPA, "_read_path", counted_read)
    calls = 0
    assert privacy_ops.retry_pending_audits(limit=2) == 0
    assert calls == 2
    assert reads == 2


def test_audit_retry_cursor_rotates_past_nonpending_records(monkeypatch):
    import maverick.audit as audit

    monkeypatch.setattr(privacy_ops, "_AUDIT_RETRY_CURSORS", {})
    monkeypatch.setattr(privacy_ops, "_AUDIT_RETRY_STORE_OFFSET", 0)
    captured = []
    monkeypatch.setattr(
        audit,
        "record",
        lambda kind, **payload: captured.append(payload["event_id"]) or True,
    )
    privacy_ops._DPA.save({
        "id": "DPA-0000000000",
        "status": "pending_review",
        "created_at": 1,
    })
    privacy_ops._DPA.save({
        "id": "DPA-1111111111",
        "status": "pending_review",
        "created_at": 2,
        "_audit_pending": [{
            "event_id": "stable-event-id",
            "action": "create",
            "actor": "system",
            "prepared_at": 1.0,
        }],
    })

    # The first one-record pass visits a record with no outbox receipt. The
    # persistent filename/store cursors make the next one-record pass advance
    # rather than rescanning that lexical prefix forever.
    assert privacy_ops.retry_pending_audits(limit=1) == 0
    assert captured == []
    assert privacy_ops.retry_pending_audits(limit=1) == 1
    assert captured == ["stable-event-id"]


class TestDepthHooks:
    """The workspace feeding itself: AIRA -> registry, connected sources ->
    DPA review, OneTrust export -> RoPA."""

    def _aira(self, prohibited="no", high_risk="no",
              purpose_note="conversational assistant for customer tickets"):
        from maverick.assessment import AssessmentSession, save_session
        s = AssessmentSession(type="aira", subject="Support triage bot")
        s.record("aira_purpose", "yes", purpose_note)
        s.record("aira_prohibited", prohibited)
        s.record("aira_high_risk", high_risk)
        save_session(s)
        return s

    def test_register_from_assessment_carries_purpose_and_provenance(self):
        s = self._aira()
        r = privacy_ops.register_ai_system_from_assessment(s.id)
        assert r["name"] == "Support triage bot"
        assert r["purpose"] == "conversational assistant for customer tickets"
        assert r["assessment_id"] == s.id
        # Keyword screening alone: a support assistant is Art. 50 limited.
        assert r["tier"] == "limited"

    def test_attestations_ratchet_tier_upward_only(self):
        s = self._aira(high_risk="yes")
        r = privacy_ops.register_ai_system_from_assessment(s.id)
        assert r["tier"] == "high"
        assert any("Annex III" in sig for sig in r["signals"])
        s2 = self._aira(prohibited="yes")
        r2 = privacy_ops.register_ai_system_from_assessment(s2.id)
        assert r2["tier"] == "prohibited"
        assert privacy_ops.register_ai_system_from_assessment("nope") is None

    def test_review_dpa_from_document_text_and_docx(self, monkeypatch):
        from maverick import doc_discovery

        def fake_fetch(source, doc_id, ref=None, **kw):
            if doc_id == "plain":
                return GOOD_DPA.encode(), "text/plain"
            if doc_id == "word":
                import io
                import zipfile
                buf = io.BytesIO()
                body = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
                               for line in GOOD_DPA.splitlines())
                with zipfile.ZipFile(buf, "w") as z:
                    z.writestr("word/document.xml",
                               f"<w:document>{body}</w:document>")
                return (buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document")
            return b"%PDF-1.7 binary", "application/pdf"

        monkeypatch.setattr(doc_discovery, "fetch", fake_fetch)
        r = privacy_ops.review_dpa_from_document(
            "Acme Corp", "msgraph", "plain", document_name="acme-dpa.txt")
        assert r["clauses_present"] >= 9
        assert r["document_name"] == "acme-dpa.txt"
        r2 = privacy_ops.review_dpa_from_document(
            "Acme Corp", "msgraph", "word", document_name="acme-dpa.docx")
        assert r2["clauses_present"] >= 9
        with pytest.raises(ValueError, match="cannot extract text"):
            privacy_ops.review_dpa_from_document(
                "Acme Corp", "msgraph", "scan", document_name="scan.pdf")

    def test_docx_zip_bomb_is_rejected_before_entry_read(self, monkeypatch):
        import io
        import zipfile

        buf = io.BytesIO()
        oversized = b"<w:document>" + (
            b"A" * privacy_ops._MAX_DOCX_DOCUMENT_XML_BYTES
        ) + b"</w:document>"
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", oversized)

        opened = False
        original_open = zipfile.ZipFile.open

        def tracked_open(self, *args, **kwargs):
            nonlocal opened
            opened = True
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(zipfile.ZipFile, "open", tracked_open)
        assert privacy_ops._document_text(
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document",
        ) == ""
        assert opened is False

    def test_import_onetrust_ropa_maps_drifting_headers(self):
        csv_text = (
            '"Processing Activity Name","Purpose of Processing",'
            '"Personal Data Categories","Cross-Border Transfers",'
            '"Retention Period","Irrelevant Column"\n'
            '"CRM marketing","Campaign targeting","Contact details, usage",'
            '"SCCs to US vendors","24 months","x"\n'
            '"","orphan row without a name","","","",""\n'
            '"Payroll","Salary processing","Bank details","None",'
            '"7 years","y"\n')
        imported = privacy_ops.import_onetrust_ropa(csv_text)
        assert [e["activity"] for e in imported] == ["CRM marketing",
                                                     "Payroll"]
        assert imported[0]["transfers"] == "SCCs to US vendors"
        assert all(e["source"] == "onetrust" for e in imported)
        # Imported rows land in the same register the CSV export reads.
        assert {e["activity"] for e in privacy_ops.export_ropa_art30()} == {
            "CRM marketing", "Payroll"}


class TestPdfExtraction:
    """DPA review straight from a digital PDF — the format DPAs actually
    arrive in. Scanned PDFs still refuse honestly."""

    @staticmethod
    def _pdf(lines, compress=False):
        import zlib
        body = "\n".join(
            f"BT /F1 10 Tf 54 700 Td ({ln}) Tj ET" for ln in lines).encode()
        if compress:
            comp = zlib.compress(body)
            return (b"%PDF-1.4\n1 0 obj\n<< /Length "
                    + str(len(comp)).encode()
                    + b" /Filter /FlateDecode >>\nstream\n" + comp
                    + b"\nendstream\nendobj\n%%EOF")
        return (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(body)).encode()
                + b" >>\nstream\n" + body + b"\nendstream\nendobj\n%%EOF")

    def test_review_dpa_from_pdf_compressed_and_not(self, monkeypatch):
        from maverick import doc_discovery
        lines = [ln for ln in GOOD_DPA.splitlines() if ln]
        for compress in (False, True):
            pdf = self._pdf(lines, compress=compress)
            monkeypatch.setattr(doc_discovery, "fetch",
                                lambda *a, _p=pdf, **k: (_p,
                                                         "application/pdf"))
            r = privacy_ops.review_dpa_from_document(
                "Acme Corp", "msgraph", "d1", document_name="acme-dpa.pdf")
            assert r["clauses_present"] >= 9, (compress, r)

    def test_tj_arrays_and_hex_strings(self):
        s = (b"BT [(Standard )(Contractual )(Clauses )(cover )(the )"
             b"(international )(transfers )(of )(personal )(data.)] TJ ET "
             b"BT <41756469742072696768747320616e6420726574656e74696f6e2073"
             b"6368656475"
             b"6c6520646566696e65642e> Tj ET")
        txt = privacy_ops._pdf_text(b"%PDF-1.4\nstream\n" + s + b"\nendstream")
        assert ("Standard Contractual Clauses cover the international "
                "transfers of personal data.") in txt
        assert "Audit rights and retention schedule defined." in txt

    def test_scanned_pdf_still_refuses(self, monkeypatch):
        from maverick import doc_discovery
        monkeypatch.setattr(
            doc_discovery, "fetch",
            lambda *a, **k: (b"%PDF-1.4\nstream\n\x00\x89PNG\x01\x02"
                             b"\nendstream", "application/pdf"))
        with pytest.raises(ValueError, match="cannot extract text"):
            privacy_ops.review_dpa_from_document("Acme", "msgraph", "scan")


class TestDsarIntake:
    """Inbound messages become tracked requests — deterministically, with
    the matched phrases kept as provenance."""

    def test_detects_each_kind_with_signals(self):
        erase = privacy_ops.detect_dsar(
            "Hi, under GDPR Article 17 please delete my data and confirm.",
            sender="sam@example.com")
        assert erase["kind"] == "erasure"
        assert erase["subject_id"] == "sam@example.com"
        assert any("delete my data" in s for s in erase["signals"])
        access = privacy_ops.detect_dsar(
            "I would like a copy of my personal data you hold on me. "
            "Regards, jordan.ellis@example.com")
        assert access["kind"] == "access"
        assert access["subject_id"] == "jordan.ellis@example.com"
        port = privacy_ops.detect_dsar(
            "Please transfer my data to my new provider (data portability).",
            sender="pat@example.com")
        assert port["kind"] == "portability"
        # Erasure phrasing wins over an access-ish word later in the text.
        both = privacy_ops.detect_dsar(
            "Right to be forgotten: erase my data. I previously made an "
            "access request.", sender="x@example.com")
        assert both["kind"] == "erasure"

    def test_ordinary_mail_is_not_a_request(self):
        assert privacy_ops.detect_dsar(
            "Hey team, the Q3 report deletes the old slide deck.") is None
        assert privacy_ops.detect_dsar("") is None

    def test_open_from_message_carries_provenance(self):
        rec = privacy_ops.open_dsar_from_message(
            "Subject: please erase my account\n\nUnder Article 17 I ask "
            "you to remove my personal data.\n-- casey@example.com",
            channel="email")
        assert rec["kind"] == "erasure"
        assert rec["subject_id"] == "casey@example.com"
        assert rec["status"] == "open"
        assert rec["intake"]["signals"]
        assert "erase my account" in rec["intake"]["excerpt"]
        # No subject anywhere -> no request opened.
        assert privacy_ops.open_dsar_from_message(
            "please delete my data thanks") is None


class TestIncidentRegister:
    """Art. 33/34: a 72-hour clock the register tracks, and a notification
    decision that stays a documented human call either way."""

    def test_open_starts_the_72h_clock(self):
        r = privacy_ops.open_incident(
            "Misdirected owner statement batch", severity="high",
            categories="contact details, account balances",
            affected_estimate="~120 owners", reported_by="ops")
        assert r["status"] == "open"
        listed = privacy_ops.list_incidents()[0]
        assert 70 <= listed["hours_left"] <= 72
        assert listed["clock_breached"] is False
        with pytest.raises(ValueError):
            privacy_ops.open_incident("x", severity="catastrophic")
        with pytest.raises(ValueError):
            privacy_ops.open_incident("   ")

    def test_notification_decision_documents_both_ways(self):
        r = privacy_ops.open_incident("Lost badge with cached emails")
        doc = privacy_ops.decide_incident_notification(
            r["id"], False, rationale="device encrypted, remotely wiped; "
            "no risk to rights and freedoms", decided_by="dpo")
        assert doc["status"] == "documented"
        assert doc["notification"]["notifiable"] is False
        assert "encrypted" in doc["notification"]["rationale"]
        r2 = privacy_ops.open_incident("Processor breach at Initech",
                                       severity="high")
        notify = privacy_ops.decide_incident_notification(
            r2["id"], True, rationale="personal data exfiltrated",
            decided_by="dpo")
        assert notify["status"] == "notify"
        closed = privacy_ops.close_incident(r2["id"], closed_by="dpo")
        assert closed["status"] == "closed"
        assert privacy_ops.decide_incident_notification("nope", True) is None

    def test_clock_breach_flags_only_undecided_open_incidents(self, monkeypatch):
        import time as _t
        undecided = privacy_ops.open_incident("Slow-burn misconfig")
        decided = privacy_ops.open_incident("Contained laptop loss")
        privacy_ops.decide_incident_notification(decided["id"], False,
                                                 rationale="contained")
        real = _t.time
        monkeypatch.setattr(_t, "time", lambda: real() + 80 * 3600)
        listed = {x["id"]: x for x in privacy_ops.list_incidents()}
        assert listed[undecided["id"]]["clock_breached"] is True
        assert listed[decided["id"]]["clock_breached"] is False
