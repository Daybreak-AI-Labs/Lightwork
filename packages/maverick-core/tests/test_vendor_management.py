"""The vendor register: records, questionnaires, monitoring, OneTrust import.

The assertions that matter: the same vendor name is one record, never a
duplicate; questionnaires only mint for registered assessment templates and
read hosted-form submissions back; and the OneTrust importer never
overwrites a vendor already registered here.
"""
from __future__ import annotations

import time

import pytest
from maverick import config
from maverick import vendor_management as vm
from maverick.governed_records import RecordConflict


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(
        "[vendor_management]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def _register(name="Acme Analytics", criticality="high"):
    return vm.register_vendor(
        name, category="analytics", criticality=criticality,
        owner="procurement", actor="ops",
    )


def test_register_is_off_by_default(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert vm.enabled() is False
    with pytest.raises(vm.VendorManagementConfigError, match="enable"):
        _register()


def test_same_name_is_one_record_never_a_duplicate():
    record = _register()
    assert record["id"].startswith("VMR-")
    assert record["status"] == "active"
    with pytest.raises(RecordConflict):
        _register()
    # Same identity modulo case/whitespace: still the same record id.
    with pytest.raises(RecordConflict):
        vm.register_vendor(
            "  acme analytics ", category="analytics", criticality="low",
            owner="procurement", actor="ops",
        )
    assert vm.get_vendor(record["id"])["name"] == "Acme Analytics"


def test_lifecycle_and_criticality_are_validated():
    with pytest.raises(ValueError, match="criticality"):
        _register(criticality="severe")
    record = _register()
    moved = vm.set_vendor_status(
        record["id"], "under_review", actor="ops",
        expected_revision=record["revision"],
    )
    assert moved["status"] == "under_review"
    with pytest.raises(ValueError, match="status"):
        vm.set_vendor_status(
            record["id"], "terminated", actor="ops",
            expected_revision=moved["revision"],
        )
    with pytest.raises(KeyError):
        vm.set_vendor_status(
            "VMR-" + "0" * 24, "active", actor="ops", expected_revision=1,
        )


def test_questionnaire_requires_a_registered_template_and_reads_back():
    record = _register()
    with pytest.raises(ValueError, match="registered template"):
        vm.request_questionnaire(
            record["id"], assessment_type="vibes", actor="ops",
            expected_revision=record["revision"],
        )
    saved = vm.request_questionnaire(
        record["id"], assessment_type="vendor_risk", actor="ops",
        expected_revision=record["revision"],
    )
    assert saved["form_path"].startswith("/form/")
    token = saved["form_path"].removeprefix("/form/")

    rows = vm.questionnaire_responses(record["id"])
    assert rows[0]["status"] == "sent"
    assert rows[0]["submissions"] == []

    from maverick import form_store

    form_store.append(token, {"q1": "yes", "q2": "no"})
    rows = vm.questionnaire_responses(record["id"])
    assert rows[0]["status"] == "answered"
    assert rows[0]["submissions"][0]["q1"] == "yes"


def test_monitoring_notes_and_posture():
    record = _register()
    with pytest.raises(ValueError, match="severity"):
        vm.record_monitoring_note(
            record["id"], "breach in the news", severity="urgent",
            actor="ops", expected_revision=record["revision"],
        )
    saved = vm.record_monitoring_note(
        record["id"], "breach in the news", severity="critical",
        actor="ops", expected_revision=record["revision"],
    )
    assert saved["monitoring_notes"][0]["severity"] == "critical"
    posture = vm.vendor_posture()
    assert posture["vendors"] == 1
    assert posture["by_criticality"]["high"] == 1
    assert posture["critical_monitoring_notes"] == 1


def test_onetrust_import_maps_tiers_and_never_overwrites():
    existing = _register("Globex", criticality="critical")
    csv_text = (
        "Vendor Name,Vendor Type,Inherent Risk,Business Owner\n"
        "Initech,hosting,Tier 1,alice\n"
        "Globex,analytics,Tier 3,bob\n"
        ",broken,Tier 2,carol\n"
        "Umbrella,security,unheard-of,dave\n"
    )
    imported = vm.import_onetrust_vendors(csv_text, imported_by="ops")
    names = {row["name"] for row in imported}
    assert names == {"Initech", "Umbrella"}
    by_name = {row["name"]: row for row in vm.list_vendors()}
    assert by_name["Initech"]["criticality"] == "high"       # Tier 1
    assert by_name["Umbrella"]["criticality"] == "medium"    # unknown tier
    assert by_name["Initech"]["source"] == "onetrust"
    # The record already here stays the record of truth.
    assert by_name["Globex"]["criticality"] == "critical"
    assert by_name["Globex"]["id"] == existing["id"]


def test_contracts_track_renewals_for_the_alert_digest():
    record = _register()
    with pytest.raises(ValueError, match="kind"):
        vm.record_vendor_contract(
            record["id"], kind="handshake", reference="x", renews_at=1.0,
            actor="ops", expected_revision=record["revision"],
        )
    past = time.time() - 5 * 86400
    soon = time.time() + 10 * 86400
    far = time.time() + 300 * 86400
    saved = vm.record_vendor_contract(
        record["id"], kind="dpa", reference="doc://dpa", renews_at=past,
        actor="ops", expected_revision=record["revision"],
    )
    saved = vm.record_vendor_contract(
        saved["id"], kind="msa", reference="doc://msa", renews_at=soon,
        actor="ops", expected_revision=saved["revision"],
    )
    saved = vm.record_vendor_contract(
        saved["id"], kind="sla", reference="doc://sla", renews_at=far,
        actor="ops", expected_revision=saved["revision"],
    )
    renewals = vm.contract_renewals(due_soon_days=30)
    assert [entry["kind"] for entry in renewals["overdue"]] == ["dpa"]
    assert [entry["kind"] for entry in renewals["due_soon"]] == ["msa"]
    # Re-recording a kind replaces it, never duplicates.
    saved = vm.record_vendor_contract(
        saved["id"], kind="dpa", reference="doc://dpa-renewed",
        renews_at=far, actor="ops", expected_revision=saved["revision"],
    )
    assert vm.contract_renewals(due_soon_days=30)["overdue"] == []


def test_questionnaire_responses_are_scored_against_risk_answers():
    record = _register()
    saved = vm.request_questionnaire(
        record["id"], assessment_type="vendor_risk", actor="ops",
        expected_revision=record["revision"],
    )
    token = saved["form_path"].removeprefix("/form/")
    from maverick.assessment import TEMPLATES

    questions = TEMPLATES["vendor_risk"].questions
    # Answer every question SAFELY except the first, answered with its
    # risk answer -- the scored rating must be that question's severity.
    fields = {}
    for index, question in enumerate(questions):
        risky = str(question.risk_answer).strip().lower()
        safe = "no" if risky == "yes" else "yes"
        fields[question.id] = risky if index == 0 else safe
    from maverick import form_store

    form_store.append(token, fields)
    row = vm.questionnaire_responses(record["id"])[0]
    assert row["status"] == "answered"
    score = row["score"]
    assert score["answered"] == len(questions)
    assert [finding["qid"] for finding in score["findings"]] == [
        questions[0].id
    ]
    assert score["rating"] == questions[0].severity
    # All-safe answers score clear.
    form_store.append(token, {
        question.id: ("no" if str(question.risk_answer).lower() == "yes"
                       else "yes")
        for question in questions
    })
    row = vm.questionnaire_responses(record["id"])[0]
    assert row["score"]["rating"] == "clear"
    assert row["score"]["findings"] == []


def test_questionnaire_for_token_resolves_only_its_own_form():
    record = _register()
    saved = vm.request_questionnaire(
        record["id"], assessment_type="vendor_risk", actor="ops",
        expected_revision=record["revision"],
    )
    token = saved["form_path"].removeprefix("/form/")
    resolved = vm.questionnaire_for_token(token)
    assert resolved["vendor_id"] == record["id"]
    assert resolved["assessment_type"] == "vendor_risk"
    assert resolved["questions"][0]["qid"]
    assert "risk_answer" not in resolved["questions"][0]
    assert vm.questionnaire_for_token("not-a-token") is None
    assert vm.questionnaire_for_token("") is None


def test_questionnaire_refuses_a_template_wider_than_the_form_store():
    # The hosted form persists one submission per form; a template with
    # more questions than the store accepts fields must refuse UP FRONT,
    # not truncate the vendor's answers at submission time.
    from maverick import form_store

    record = _register()
    with pytest.raises(ValueError, match="questions"):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(form_store, "_MAX_FIELDS", 3)
            vm.request_questionnaire(
                record["id"], assessment_type="vendor_risk", actor="ops",
                expected_revision=record["revision"],
            )


def test_scoring_mirrors_the_assessment_answer_vocabulary():
    # "na" clears a question; "unknown" raises an unverified finding at
    # the question's severity -- the same contract as assessment.evaluate.
    from maverick.assessment import TEMPLATES

    questions = TEMPLATES["vendor_risk"].questions
    fields = {}
    for index, question in enumerate(questions):
        risky = str(question.risk_answer).strip().lower()
        safe = "no" if risky == "yes" else "yes"
        fields[question.id] = {0: "na", 1: "unknown"}.get(index, safe)
    score = vm._score_submission("vendor_risk", fields)
    assert score["answered"] == len(questions)
    assert [f["qid"] for f in score["findings"]] == [questions[1].id]
    assert score["findings"][0]["kind"] == "unverified"
    assert score["rating"] == questions[1].severity
