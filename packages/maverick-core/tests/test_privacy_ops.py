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
