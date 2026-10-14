from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from maverick import audit
from maverick import trc_continuous_evidence as evidence
from maverick_dashboard import grc_studio_report_views as reports


def test_published_report_uses_current_retained_evidence_and_limits_fields(monkeypatch):
    now = time.time()
    plan = {
        "label": "Security checks",
        "definition_id": "security-definition",
        "definition_version": 3,
        "collector_id": "security-checks",
        "enrollment_id": "security-enrollment",
        "credential_ready": True,
        "asset_scope_sha256": "a" * 64,
        "credential_profile_sha256": "b" * 64,
    }
    definition = SimpleNamespace(
        definition_id="security-definition",
        version=3,
        state="approved",
        collector_id="security-checks",
        integrity_sha256="c" * 64,
        allowed_control_refs=("access-control",),
    )
    enrollment = SimpleNamespace(
        enrollment_id="security-enrollment",
        state="active",
        revision=4,
        definition_id="security-definition",
        definition_version=3,
        definition_integrity_sha256="c" * 64,
        integrity_sha256="d" * 64,
        collector_id="security-checks",
        asset_scope_sha256="a" * 64,
        credential_profile_sha256="b" * 64,
        control_refs=("access-control",),
    )
    run = SimpleNamespace(
        run_id="run-1",
        state="completed",
        complete=True,
        retained_until=now + 3_600,
        enrollment_id=enrollment.enrollment_id,
        enrollment_revision=enrollment.revision,
        definition_id=definition.definition_id,
        definition_version=definition.version,
        collector_id=definition.collector_id,
    )
    observations = (
        SimpleNamespace(
            observation_id="observation-1",
            run_id=run.run_id,
            control_ref="access-control",
            status="observed",
            reason_code="verified",
            evidence_sha256="e" * 64,
            source_revision_sha256="f" * 64,
            observed_at=now - 30,
            retained_until=now + 3_600,
        ),
        SimpleNamespace(
            observation_id="observation-2",
            run_id=run.run_id,
            control_ref="access-control",
            status="failed",
            reason_code="not_verified",
            evidence_sha256="1" * 64,
            source_revision_sha256="2" * 64,
            observed_at=now - 20,
            retained_until=now + 3_600,
        ),
    )

    enrollment_reads = {"count": 0, "change_during_read": False}

    class EvidenceStore:
        def list_runs(self, *, enrollment_id, limit):
            assert enrollment_id == enrollment.enrollment_id
            assert limit == 1_000
            return (run,)

        def list_observations(self, *, enrollment_id, limit):
            assert enrollment_id == enrollment.enrollment_id
            assert limit == 200
            return observations

        def get_enrollment(self, enrollment_id):
            assert enrollment_id == enrollment.enrollment_id
            enrollment_reads["count"] += 1
            if enrollment_reads["change_during_read"] and enrollment_reads["count"] > 1:
                return SimpleNamespace(**{**vars(enrollment), "integrity_sha256": "9" * 64})
            return enrollment

        def get_definition(self, definition_id, version):
            assert (definition_id, version) == (definition.definition_id, definition.version)
            return definition

    evidence_store = EvidenceStore()
    binding = {
        "object_id": "controls",
        "collector_id": definition.collector_id,
        "collector_version": definition.version,
        "collector_integrity_sha256": definition.integrity_sha256,
        "control_refs": ["access-control"],
        "field_mappings": [
            {
                "source_path": ["status"],
                "target_field_id": "result",
                "mode": "replace",
            },
            {
                "source_path": ["observed_at"],
                "target_field_id": "checked_at",
                "mode": "replace",
            },
            {
                "source_path": ["evidence_sha256"],
                "target_field_id": "restricted_digest",
                "mode": "replace",
            },
        ],
    }
    fields = [
        {
            "field_id": "result",
            "label_ref": "check-result",
            "field_type": "single_choice",
            "sensitivity": "internal",
            "options": ["observed", "failed", "unknown"],
        },
        {
            "field_id": "checked_at",
            "label_ref": "checked-at",
            "field_type": "timestamp",
            "sensitivity": "internal",
        },
        {
            "field_id": "restricted_digest",
            "label_ref": "restricted-digest",
            "field_type": "digest",
            "sensitivity": "restricted",
        },
    ]
    view = {
        "view_id": "current-checks",
        "object_id": "controls",
        "title_ref": "current-checks-title",
        "columns": ["result", "checked_at"],
        "filters": [{"field_id": "result", "operator": "equals", "value": "observed"}],
        "group_by": ["result"],
        "sort": [{"field_id": "checked_at", "direction": "descending"}],
    }
    blueprint = {
        "objects": [{"object_id": "controls", "fields": fields}],
        "report_views": [view],
        "collector_bindings": [binding],
    }
    pointer = SimpleNamespace(active_version=1, integrity_sha256="pointer-integrity")
    version = SimpleNamespace(
        version=1,
        state="published",
        blueprint=blueprint,
        integrity_sha256="version-integrity",
    )
    studio_store = SimpleNamespace(get_active=lambda *_args: (pointer, version))

    from maverick_dashboard import auth, grc_draft_changes, trc_setup_api

    page_checks = []
    audit_events = []
    monkeypatch.setattr(auth, "require_page_permission", lambda _request, page: page_checks.append(page))
    monkeypatch.setattr(auth, "caller_principal", lambda _request: "user:alice")
    monkeypatch.setattr(auth, "has_global_permission", lambda *_args: False)
    monkeypatch.setattr(auth, "suite_allowed", lambda *_args: True)
    monkeypatch.setattr(trc_setup_api, "_store", lambda *_args: (evidence, "tenant-a", evidence_store))
    monkeypatch.setattr(trc_setup_api, "_manifests", lambda *_args: {enrollment.enrollment_id: plan})
    monkeypatch.setattr(grc_draft_changes, "context", lambda *_args: "same-account-and-workspace")
    monkeypatch.setattr(
        audit,
        "audit_event",
        lambda kind, **payload: audit_events.append((kind, payload)) or True,
    )
    monkeypatch.setitem(
        sys.modules,
        "maverick_dashboard.app",
        SimpleNamespace(require_page_visible=lambda *_args: None),
    )

    result = reports.preview_published_report(
        request=object(),
        tenant="tenant-a",
        studio_store=studio_store,
        studio_id="security-studio",
        version_number=1,
        view_id=view["view_id"],
        limit=200,
    )

    assert result["state"] == "ready"
    assert "/compliance/grc-studio" in page_checks
    assert "/compliance/continuous-evidence" in page_checks
    assert result["row_count"] == 1
    assert result["matched_row_count"] == 1
    assert result["read_only"] is True
    assert result["sources"] == ["Security checks"]
    assert "not a compliance conclusion" in result["note"]
    assert [column["field_id"] for column in result["columns"]] == ["result", "checked_at"]
    assert result["rows"] == [
        {"values": {"result": "observed", "checked_at": observations[0].observed_at}}
    ]
    assert "restricted_digest" not in result["rows"][0]["values"]
    assert len(audit_events) == 1
    audit_kind, audit_payload = audit_events[0]
    assert audit_kind == "control_plane_action"
    assert audit_payload == {
        "agent": "dashboard",
        "action": "grc_report_preview_returned",
        "actor": "user:alice",
        "tenant": "tenant-a",
        "status": "ready",
        "studio_id": "security-studio",
        "studio_version": 1,
        "report_view_id": "current-checks",
        "source_count": 1,
        "matched_row_count": 1,
        "returned_row_count": 1,
        "access_channel": "native",
    }
    assert not {"rows", "filters", "values", "evidence_sha256"} & audit_payload.keys()

    binding["field_mappings"][0]["source_path"] = ["provider_custom_output"]
    with pytest.raises(HTTPException) as unsupported:
        reports.preview_published_report(
            request=object(),
            tenant="tenant-a",
            studio_store=studio_store,
            studio_id="security-studio",
            version_number=1,
            view_id=view["view_id"],
            limit=200,
        )
    assert unsupported.value.status_code == 409
    binding["field_mappings"][0]["source_path"] = ["status"]

    view["columns"] = ["restricted_digest"]
    with pytest.raises(HTTPException) as denied:
        reports.preview_published_report(
            request=object(),
            tenant="tenant-a",
            studio_store=studio_store,
            studio_id="security-studio",
            version_number=1,
            view_id=view["view_id"],
            limit=200,
        )
    assert denied.value.status_code == 403

    view["columns"] = ["result"]
    enrollment_reads.update(count=0, change_during_read=True)
    with pytest.raises(HTTPException) as changed:
        reports.preview_published_report(
            request=object(),
            tenant="tenant-a",
            studio_store=studio_store,
            studio_id="security-studio",
            version_number=1,
            view_id=view["view_id"],
            limit=200,
        )
    assert changed.value.status_code == 409
    assert len(audit_events) == 1

    enrollment_reads.update(count=0, change_during_read=False)
    monkeypatch.setattr(audit, "audit_event", lambda *_args, **_kwargs: False)
    with pytest.raises(HTTPException) as unrecorded:
        reports.preview_published_report(
            request=object(),
            tenant="tenant-a",
            studio_store=studio_store,
            studio_id="security-studio",
            version_number=1,
            view_id=view["view_id"],
            limit=200,
        )
    assert unrecorded.value.status_code == 503
    assert "results were not shown" in unrecorded.value.detail
