"""Department first outcomes: bundled sample data a real pack can run on."""
from __future__ import annotations

import csv
import io

import pytest
from maverick import sample_outcomes as so
from maverick.domain import available_domains

DEPARTMENTS = ("finance", "privacy", "security", "legal", "hr", "procurement", "customer_support", "tax")
TEXT_SUFFIXES = (".csv", ".md")


def test_exactly_eight_departments_in_display_order():
    assert tuple(so.FIRST_OUTCOMES) == DEPARTMENTS
    assert [row["department"] for row in so.departments()] == list(DEPARTMENTS)


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_fixtures_load_and_parse(department):
    files = so.load_fixtures(department)
    outcome = so.get(department)
    assert "README.md" in files and set(outcome.files) <= set(files)
    for name, text in files.items():
        assert name.endswith(TEXT_SUFFIXES), name
        assert 0 < len(text.encode("utf-8")) < 16_000, name
        if name.endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(text)))
            assert len(rows) >= 1 and all(None not in row.values() for row in rows), name
    # The README says what a correct result contains and repeats the sample rule.
    readme = " ".join(files["README.md"].split())
    assert "Northwind Fixtures" in readme and "Synthetic" in readme
    assert "A correct result" in readme
    assert "never counts as real evidence" in readme


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_each_outcome_resolves_to_a_pack_that_can_read_its_files(department):
    outcome = so.get(department)
    packs = available_domains()
    assert outcome.pack in packs
    profile = packs[outcome.pack]
    # Every fixture is a text file; read_file is the tool that reads it.
    assert "read_file" in profile.allow_tools and "read_file" not in profile.deny_tools
    assert all(name.endswith(TEXT_SUFFIXES) for name in outcome.files)
    # The gate is the pack's own output contract, so the result lands where we say.
    assert outcome.gate == profile.output.gate
    assert outcome.lands_in == ("Approvals" if profile.output.gate == "approval" else "Results")


def test_finance_seeds_the_three_exceptions_and_expected_shape_names_them():
    files = so.load_fixtures("finance")
    invoices = list(csv.DictReader(io.StringIO(files["invoices.csv"])))
    vendors = {r["vendor"] for r in csv.DictReader(io.StringIO(files["vendor_master.csv"]))}
    pos = {r["po_number"]: r for r in csv.DictReader(io.StringIO(files["purchase_orders.csv"]))}
    by_key = {}
    duplicates, mismatches, unknown = [], [], []
    for inv in invoices:
        key = (inv["vendor"], inv["po_number"], inv["amount_usd"])
        if key in by_key:
            duplicates.append((inv["invoice_id"], by_key[key]))
        by_key.setdefault(key, inv["invoice_id"])
        if inv["vendor"] not in vendors:
            unknown.append(inv["invoice_id"])
        elif inv["amount_usd"] != pos[inv["po_number"]]["total_usd"]:
            mismatches.append(inv["invoice_id"])
    assert duplicates == [("INV-1043", "INV-1041")]
    assert mismatches == ["INV-1044"] and unknown == ["INV-1045"]
    expected = so.expected_result("finance")
    for token in ("INV-1043", "INV-1041", "INV-1044", "INV-1045", "Approvals"):
        assert token in expected


def test_sample_workspace_is_created_under_the_workdir_and_flagged(tmp_path):
    target = so.create_sample_workspace("legal", tmp_path)
    assert target == tmp_path / "sample-outcomes" / "legal"
    assert so.is_sample_workspace(target) and not so.is_sample_workspace(tmp_path)
    marker = (target / so.SAMPLE_MARKER_FILENAME).read_text(encoding="utf-8")
    assert "never counts as real evidence" in marker and "legal_nda_desk" in marker
    for rel in so.relative_paths("legal"):
        assert (tmp_path / rel).is_file(), rel
    # Re-running refreshes in place instead of failing.
    (target / "nda_playbook.md").write_text("edited", encoding="utf-8")
    so.create_sample_workspace("legal", tmp_path)
    assert "playbook" in (target / "nda_playbook.md").read_text(encoding="utf-8")


def test_sample_workspace_uses_the_configured_sandbox_workdir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_WORKDIR", str(tmp_path / "work"))
    from maverick import config
    config.reset_config_cache()
    try:
        assert so.sample_workspace_path("tax") == tmp_path / "work" / "sample-outcomes" / "tax"
    finally:
        config.reset_config_cache()


def test_goal_text_points_at_the_workspace_files_and_repeats_the_rule():
    for department in DEPARTMENTS:
        outcome = so.get(department)
        text = so.goal_description(department)
        assert so.SAMPLE_RULE in text and outcome.lands_in in text
        for rel in so.relative_paths(department):
            assert rel in text and rel.startswith("sample-outcomes/")
        assert outcome.title.startswith(so.SAMPLE_TITLE_PREFIX)
        command = so.cli_command(department)
        assert command.startswith('maverick start "Sample: ') and command.endswith(f"--domain {outcome.pack}")
        assert "--model" not in command and "claude" not in command


def test_resolve_department_accepts_labels_and_slugs_and_falls_back():
    assert so.resolve_department("Privacy & compliance") == "privacy"
    assert so.resolve_department("customer_support") == "customer_support"
    assert so.resolve_department("HR") == "hr"
    assert so.resolve_department("$5") == "finance"
    assert so.resolve_department("") == "finance"
    assert so.resolve_department("nonsense", default=None) is None


def test_goal_is_marked_as_sample_in_the_world(tmp_path):
    from maverick.world_model import WorldModel

    world = WorldModel(tmp_path / "world.db")
    goal_id = world.create_goal(so.get("hr").title, so.goal_description("hr"))
    so.mark_goal_as_sample(world, goal_id, "hr")
    goal = world.get_goal(goal_id)
    assert so.is_sample_goal(world, goal)
    events = world.goal_events(goal_id)
    assert [e.kind for e in events] == [so.SAMPLE_EVENT_KIND]
    assert "hr_onboarding" in events[0].content
    plain = world.create_goal("Reconcile the ledger", "")
    assert not so.is_sample_goal(world, world.get_goal(plain))


def test_config_knob_round_trips(tmp_path, monkeypatch):
    from maverick import config

    cfg = tmp_path / "config.toml"
    cfg.write_text('[first_outcome]\ndepartment = "security"\nsample_workspace_created = true\n', encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    config.reset_config_cache()
    try:
        assert config.get_first_outcome() == {"department": "security", "sample_workspace_created": True}
        cfg.write_text("[budget]\n", encoding="utf-8")
        config.reset_config_cache()
        assert config.get_first_outcome() == {"department": None, "sample_workspace_created": False}
    finally:
        config.reset_config_cache()
