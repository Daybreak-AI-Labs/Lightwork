"""Privacy ops record types: DPA review, AI registry, RoPA, DSAR tracker."""
from __future__ import annotations

import json

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
        privacy_ops.upsert_ropa({"retention": "12 months"}, ropa_id=rid)
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

    def test_erasure_fulfillment_is_never_destructive_from_the_api(self):
        r = privacy_ops.open_dsar("jordan@example.com", "erasure",
                                  channel="email")
        ready = privacy_ops.fulfill_dsar(r["id"])
        assert ready["status"] == "awaiting_erasure"
        assert ready["fulfillment"]["erase_command"].startswith(
            "maverick erase --user jordan@example.com")
        closed = privacy_ops.close_dsar(r["id"], closed_by="privacy-lead")
        assert closed["status"] == "closed"


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
