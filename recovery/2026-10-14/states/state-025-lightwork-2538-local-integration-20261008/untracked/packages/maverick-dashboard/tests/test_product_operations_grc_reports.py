from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from maverick_dashboard import product_operations as operations
from maverick_dashboard import product_operations_grc_reports as reports


def _published_version(studio_id="security-studio", version_number=2):
    view = {
        "view_id": "current-checks",
        "object_id": "controls",
        "title_ref": "current-checks",
    }
    blueprint = {
        "name_ref": "security-controls",
        "report_views": [view],
        "collector_bindings": [{"object_id": "controls"}],
    }
    version = SimpleNamespace(
        studio_id=studio_id,
        version=version_number,
        state="published",
        integrity_sha256="version-integrity",
        blueprint=blueprint,
    )
    pointer = SimpleNamespace(
        active_version=version_number,
        integrity_sha256="pointer-integrity",
    )
    return version, pointer


def test_published_report_choices_are_bounded_to_current_published_versions(monkeypatch):
    active, pointer = _published_version()

    class Store:
        def list_versions(self, tenant, *, state, limit):
            assert (tenant, state, limit) == ("tenant-a", "published", reports._REPORT_LIMIT + 1)
            return (active,)

        def get_active(self, tenant, studio_id):
            assert tenant == "tenant-a"
            assert studio_id == active.studio_id
            return pointer, active

    store = Store()
    monkeypatch.setattr(reports, "_authority", lambda _request: ("tenant-a", store, "same-context"))
    monkeypatch.setattr(reports, "_authority_context", lambda _request: "same-context")

    _revision, choices, more = reports._report_choices(object(), {})

    assert more is False
    assert len(choices) == 1
    assert choices[0]["label"] == "Security Controls · Current Checks · version 2"
    assert json.loads(choices[0]["value"]) == ["security-studio", 2, "current-checks"]


def test_published_report_choices_refuse_a_version_that_is_not_current(monkeypatch):
    active, pointer = _published_version()
    stale = SimpleNamespace(
        studio_id=active.studio_id,
        version=1,
        integrity_sha256="old-integrity",
    )

    class Store:
        def list_versions(self, tenant, *, state, limit):
            return (stale,)

        def get_active(self, tenant, studio_id):
            return pointer, active

    store = Store()
    monkeypatch.setattr(reports, "_authority", lambda _request: ("tenant-a", store, "same-context"))
    monkeypatch.setattr(reports, "_authority_context", lambda _request: "same-context")

    with pytest.raises(HTTPException) as changed:
        reports._report_choices(object(), {})

    assert changed.value.status_code == 409


def test_copilot_report_reader_is_registered_and_read_only():
    adapter = operations.adapters()["grc.report.read"]

    assert adapter.execute is None
    assert adapter.family == "artifacts"
    assert adapter.pages == (reports.PAGE,)
    assert "show compliance report results" in adapter.aliases


def test_read_report_preparation_returns_plain_saved_results(monkeypatch):
    studio_id = "s" * 100
    view_id = "v" * 100
    report = json.dumps([studio_id, 3, view_id], separators=(",", ":"))
    store = object()
    monkeypatch.setattr(reports, "_authority", lambda _request: ("tenant-a", store, "context"))
    calls = []

    def preview_published_report(**kwargs):
        calls.append(kwargs)
        return {
            "studio_name_ref": "safety",
            "title_ref": "current-checks",
            "state": "ready",
            "row_count": 1,
            "matched_row_count": 1,
            "columns": [
                {"field_id": "status", "label_ref": "check-result", "field_type": "single_choice"},
                {"field_id": "checked_at", "label_ref": "checked-at", "field_type": "timestamp"},
            ],
            "rows": [{"values": {"status": "observed", "checked_at": 0}}],
            "group_by": [{"field_id": "reason", "label_ref": "reason-code"}],
            "groups": [{"values": {"reason": "not_verified"}, "count": 1}],
            "sources": ["Approved security check"],
            "note": "Read-only results from saved technical observations.",
        }

    monkeypatch.setattr(reports, "preview_published_report", preview_published_report)

    result = reports.prepare(object(), {}, {"report": report})

    assert result.summary == "Read-only results from saved technical observations."
    prepared = result.native["grc_published_report"]
    assert prepared["title"] == "Safety \u00b7 Current Checks \u00b7 version 3"
    assert prepared["columns"] == ["Check Result", "Checked At"]
    assert prepared["rows"] == [["Check completed", "1970-01-01T00:00:00 UTC"]]
    assert prepared["group_columns"] == ["Reason Code"]
    assert prepared["groups"] == [["Not verified", 1]]
    assert prepared["more_rows"] is False
    assert calls[0]["studio_id"] == studio_id
    assert calls[0]["view_id"] == view_id
    assert calls[0]["limit"] == reports._COPILOT_ROW_LIMIT
    assert calls[0]["access_channel"] == "copilot"
