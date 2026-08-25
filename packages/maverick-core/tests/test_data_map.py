"""The data map: classification heuristics, importers, the Graph census.

The assertions that matter: the classifier flags the classic personal-data
column names and stays quiet on operational ones; health marks an asset
special-category; the information_schema importer groups columns into
per-table assets; and the SharePoint census registers what Graph returns
without ever needing live credentials in tests.
"""
from __future__ import annotations

import pytest
from maverick import config
from maverick import data_map as dm


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_GRAPH_TOKEN", raising=False)
    from maverick import audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *_args, **_kwargs: True)
    (tmp_path / "config.toml").write_text(
        "[data_map]\nenable = true\n", encoding="utf-8",
    )
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def test_map_is_off_by_default(tmp_path):
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert dm.enabled() is False
    with pytest.raises(dm.DataMapConfigError, match="enable"):
        dm.register_data_asset(
            "t", system="s", kind="table", owner="o", actor="a",
        )


def test_classifier_flags_the_classics_and_stays_quiet_otherwise():
    assert "email" in dm.classify_column("customer_email")
    assert "phone" in dm.classify_column("MobileNumber")
    assert "national_id" in dm.classify_column("ssn")
    assert "date_of_birth" in dm.classify_column("date_of_birth")
    assert "date_of_birth" in dm.classify_column("dob")
    assert "financial" in dm.classify_column("iban")
    assert "health" in dm.classify_column("primary_diagnosis_code")
    assert "credentials" in dm.classify_column("api_key_hash")
    assert "ip_address" in dm.classify_column("client_ip_addr")
    assert dm.classify_column("order_total") == []
    assert dm.classify_column("warehouse_zone") == []
    assert dm.classify_column("") == []

    flagged = dm.classify_columns(
        ["id", "email", "created_at", "phone_number"]
    )
    assert set(flagged) == {"email", "phone_number"}


def test_register_classifies_and_health_marks_special_category():
    record = dm.register_data_asset(
        "crm.contacts", system="snowflake", kind="table", owner="data-team",
        actor="ops", columns=["id", "email", "phone", "notes"],
    )
    assert record["id"].startswith("DMA-")
    assert record["categories"] == ["email", "phone"]
    assert record["special_category"] is False
    assert set(record["flagged_columns"]) == {"email", "phone"}

    # Re-registration reclassifies in place -- same id, new columns.
    again = dm.register_data_asset(
        "crm.contacts", system="snowflake", kind="table", owner="data-team",
        actor="ops", columns=["id", "email", "allergy_list"],
    )
    assert again["id"] == record["id"]
    assert again["special_category"] is True
    assert "health" in again["categories"]

    with pytest.raises(ValueError, match="kind"):
        dm.register_data_asset(
            "x", system="s", kind="blob", owner="o", actor="a",
        )


def test_links_attach_specialist_records():
    record = dm.register_data_asset(
        "hr.people", system="postgres", kind="table", owner="hr",
        actor="ops", columns=["email"],
    )
    linked = dm.link_asset_record(
        record["id"], kind="ropa", reference="ROPA-abc123",
        actor="ops", expected_revision=record["revision"],
    )
    assert linked["links"][0]["reference"] == "ROPA-abc123"
    with pytest.raises(ValueError, match="kind"):
        dm.link_asset_record(
            record["id"], kind="spreadsheet", reference="x",
            actor="ops", expected_revision=linked["revision"],
        )
    with pytest.raises(KeyError):
        dm.link_asset_record(
            "DMA-" + "0" * 24, kind="ropa", reference="x",
            actor="ops", expected_revision=1,
        )


def test_information_schema_import_groups_columns_into_tables():
    csv_text = (
        "table_schema,table_name,column_name\n"
        "crm,contacts,id\n"
        "crm,contacts,email\n"
        "crm,contacts,phone\n"
        "ops,shipments,tracking_code\n"
        "ops,shipments,dest_postcode\n"
        ",,broken\n"
    )
    result = dm.import_information_schema_csv(
        csv_text, system="snowflake", owner="data-team", imported_by="ops",
    )
    assert result == {"imported": 2, "skipped": 1}
    rows = dm.list_data_assets(system="snowflake")
    by_name = {row["name"]: row for row in rows}
    assert by_name["crm.contacts"]["categories"] == ["email", "phone"]
    assert by_name["ops.shipments"]["categories"] == ["address"]
    assert by_name["crm.contacts"]["column_count"] == 3

    posture = dm.data_map_posture()
    assert posture["assets"] == 2
    assert posture["flagged_assets"] == 2
    assert posture["unlinked_flagged_assets"] == 2
    assert posture["by_category"]["email"] == 1


def test_sharepoint_census_registers_what_graph_returns():
    calls: list[str] = []

    def fetcher(url: str) -> dict:
        calls.append(url)
        return {
            "value": [
                {"displayName": "Legal Hub",
                 "webUrl": "https://contoso.sharepoint.com/sites/legal"},
                {"name": "ops-archive",
                 "webUrl": "https://contoso.sharepoint.com/sites/ops"},
                {"webUrl": "https://contoso.sharepoint.com/anon"},
                "not-a-site",
            ]
        }

    result = dm.sync_sharepoint_sites(
        actor="ops", owner="it", search="hub", fetcher=fetcher,
    )
    assert result == {"imported": 2, "skipped": 2}
    assert calls == ["https://graph.microsoft.com/v1.0/sites?search=hub"]
    rows = dm.list_data_assets(system="sharepoint")
    assert {row["name"] for row in rows} == {"Legal Hub", "ops-archive"}
    assert rows[0]["kind"] == "site"
    assert rows[0]["source"] == "microsoft_graph"

    # No query -> the tenant-wide census endpoint.
    dm.sync_sharepoint_sites(actor="ops", owner="it", fetcher=fetcher)
    assert calls[-1] == "https://graph.microsoft.com/v1.0/sites/getAllSites"


def test_sharepoint_census_without_a_token_is_a_config_error():
    with pytest.raises(dm.DataMapConfigError, match="Sites.Read.All"):
        dm.sync_sharepoint_sites(actor="ops", owner="it")


def test_malformed_graph_payload_is_an_error():
    with pytest.raises(dm.DataMapError, match="unexpected shape"):
        dm.sync_sharepoint_sites(
            actor="ops", owner="it", fetcher=lambda _url: {"value": "nope"},
        )


def test_classifier_anchors_stay_quiet_on_lookalike_columns():
    # Substrings that used to false-positive: the anchored patterns must
    # leave operational columns alone.
    assert dm.classify_column("mail_server") == []
    assert dm.classify_column("microphone_enabled") == []
    assert dm.classify_column("healthcheck_status") == []
    assert dm.classify_column("shipment_status") == []
    # ...while the genuine articles (snake or camel) still flag.
    assert "email" in dm.classify_column("EmailAddress")
    assert "phone" in dm.classify_column("telephone_number")
    assert "health" in dm.classify_column("health_condition")


def test_identifying_categories_subset_the_classifier_vocabulary():
    assert set(dm.CLASSIFIER_CATEGORIES) >= dm.IDENTIFYING_CATEGORIES


def test_schema_import_checks_capacity_once_for_the_batch(monkeypatch):
    dm.register_data_asset(
        "warehouse.existing", system="snowflake", kind="table",
        owner="data-team", actor="ops",
    )
    monkeypatch.setattr(dm, "_MAX_RECORDS", 1)
    csv_text = (
        "table_schema,table_name,column_name\n"
        "warehouse,existing,email\n"
        "warehouse,brand_new,order_id\n"
    )
    result = dm.import_information_schema_csv(
        csv_text, system="snowflake", owner="data-team",
    )
    # The existing table refreshes in place; the new one has no budget
    # left and is skipped instead of raising mid-batch.
    assert result == {"imported": 1, "skipped": 1}
    names = {row["name"] for row in dm.list_data_assets()}
    assert names == {"warehouse.existing"}
