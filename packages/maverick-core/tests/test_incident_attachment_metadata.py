from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from maverick import governed_document_vault as document_vault
from maverick import incident_attachment_metadata as iam
from maverick.paths import tenant_scope


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def test_stock_media_contract_matches_governed_document_vault() -> None:
    assert iam.DEFAULT_MEDIA_TYPES == document_vault.DEFAULT_MEDIA_TYPES


def _fixture_store(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORDS_BACKEND", "local")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    clock = [time.time()]
    principal = ["incident-principal-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    incident = {
        "kind": "privacy",
        "id": "INC-privacy-one",
        "revision": 3,
        "sha256": _digest("incident-v3"),
        "revoked": False,
    }
    document = {
        "id": "DOC-evidence-one",
        "revision": 7,
        "authority_sha256": _digest("document-authority-v7"),
        "content_sha256": _digest("document-content"),
        "safe_display_name": "incident-report.pdf",
        "media_type": "application/pdf",
        "byte_size": 4_096,
        "download_allowed": True,
    }

    def incident_resolver(tenant, principal_ref, kind, incident_id):
        assert tenant == "alpha"
        return iam.issue_incident_authority(
            tenant_id=tenant,
            principal_ref=principal_ref,
            incident_kind=kind,
            incident_id=incident_id,
            incident_revision=incident["revision"],
            incident_sha256=incident["sha256"],
            revoked=incident["revoked"],
        )

    def document_resolver(tenant, principal_ref, document_id):
        return SimpleNamespace(
            tenant_id=tenant,
            principal_ref=principal_ref,
            document_ref=document_id,
            authority_revision=document["revision"],
            authority_sha256=document["authority_sha256"],
            safe_display_name=document["safe_display_name"],
            media_type=document["media_type"],
            byte_size=document["byte_size"],
            content_sha256=document["content_sha256"],
            download_allowed=document["download_allowed"],
        )

    store = iam.IncidentAttachmentStore(
        tenant_id="alpha",
        principal_ref=principal[0],
        principal_resolver=lambda: principal[0],
        incident_authority_resolver=incident_resolver,
        document_authority_resolver=document_resolver,
        maintenance_authorizer=lambda: True,
        config_gate=lambda: None,
        retention_seconds=3_600,
        attachment_ttl_seconds=600,
        clock=lambda: clock[0],
    )
    return store, clock, principal, incident, document


def _create(store, incident, document, *, key="create-one", redaction="unreviewed"):
    return store.create_attachment(
        "alpha",
        incident_kind=incident["kind"],
        incident_id=incident["id"],
        incident_revision=incident["revision"],
        incident_sha256=incident["sha256"],
        document_id=document["id"],
        document_revision=document["revision"],
        document_authority_sha256=document["authority_sha256"],
        sha256=document["content_sha256"],
        redaction_state=redaction,
        idempotency_key=key,
    )


def test_create_is_closed_authority_bound_and_idempotent(tmp_path, monkeypatch):
    store, _clock, _principal, incident, document = _fixture_store(
        tmp_path, monkeypatch
    )
    with tenant_scope(tenant="alpha"):
        created = _create(store, incident, document)
        replay = _create(store, incident, document)

        assert replay == created
        assert created.attachment_id.startswith("IAT-")
        assert created.incident_revision == 3
        assert created.document_revision == 7
        assert created.safe_display_name == "incident-report.pdf"
        assert created.media_type == "application/pdf"
        assert created.byte_size == 4_096
        assert created.sha256 == document["content_sha256"]
        assert created.revision == 1
        assert store.backend_kind == "local"
        assert store.verify_integrity("alpha")["ok"] is True
        raw = store._records.get(created.attachment_id)  # noqa: SLF001
        assert raw is not None
        encoded = json.dumps(raw).lower()
        assert all(
            forbidden not in encoded
            for forbidden in ("filesystem_path", "object_store_uri", "credential", "raw_bytes")
        )

        with pytest.raises(iam.IncidentAttachmentConflict, match="idempotency"):
            _create(store, incident, document, redaction="pending")


def test_exact_authority_media_and_scope_fail_closed(tmp_path, monkeypatch):
    store, _clock, principal, incident, document = _fixture_store(tmp_path, monkeypatch)
    with tenant_scope(tenant="alpha"):
        with pytest.raises(iam.IncidentAttachmentConflict, match="incident revision"):
            store.create_attachment(
                "alpha",
                incident_kind="privacy",
                incident_id=incident["id"],
                incident_revision=2,
                incident_sha256=incident["sha256"],
                document_id=document["id"],
                document_revision=document["revision"],
                document_authority_sha256=document["authority_sha256"],
                sha256=document["content_sha256"],
                idempotency_key="wrong-incident",
            )
        with pytest.raises(iam.IncidentAttachmentConflict, match="document revision"):
            store.create_attachment(
                "alpha",
                incident_kind="privacy",
                incident_id=incident["id"],
                incident_revision=incident["revision"],
                incident_sha256=incident["sha256"],
                document_id=document["id"],
                document_revision=6,
                document_authority_sha256=document["authority_sha256"],
                sha256=document["content_sha256"],
                idempotency_key="wrong-document",
            )
        document["media_type"] = "application/x-msdownload"
        with pytest.raises(iam.IncidentAttachmentValidationError, match="not allowed"):
            _create(store, incident, document, key="blocked-media")
        document["media_type"] = "application/pdf"
        with pytest.raises(iam.IncidentAttachmentNotFound):
            store.list_for_incident("beta", "privacy", incident["id"])
        principal[0] = "incident-principal-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        with pytest.raises(iam.IncidentAttachmentPermissionDenied):
            store.list_for_incident("alpha", "privacy", incident["id"])


def test_update_rebinds_current_incident_and_document_with_cas(tmp_path, monkeypatch):
    store, _clock, _principal, incident, document = _fixture_store(
        tmp_path, monkeypatch
    )
    with tenant_scope(tenant="alpha"):
        created = _create(store, incident, document)
        incident["revision"] = 4
        incident["sha256"] = _digest("incident-v4")
        document["revision"] = 8
        document["authority_sha256"] = _digest("document-authority-v8")
        document["safe_display_name"] = "redacted-report.pdf"

        assert store.authority_state(created) == "stale"
        updated = store.update_attachment(
            "alpha",
            created.attachment_id,
            expected_revision=created.revision,
            incident_revision=incident["revision"],
            incident_sha256=incident["sha256"],
            document_revision=document["revision"],
            document_authority_sha256=document["authority_sha256"],
            sha256=document["content_sha256"],
            redaction_state="pending",
            idempotency_key="update-one",
        )
        replay = store.update_attachment(
            "alpha",
            created.attachment_id,
            expected_revision=created.revision,
            incident_revision=incident["revision"],
            incident_sha256=incident["sha256"],
            document_revision=document["revision"],
            document_authority_sha256=document["authority_sha256"],
            sha256=document["content_sha256"],
            redaction_state="pending",
            idempotency_key="update-one",
        )
        assert replay == updated
        assert updated.revision == 2
        assert updated.previous_integrity_sha256 == created.integrity_sha256
        assert updated.safe_display_name == "redacted-report.pdf"
        assert updated.redaction_state == "pending"
        assert store.authority_state(updated) == "active"
        assert len(store.history("alpha", created.attachment_id)) == 2

        with pytest.raises(iam.IncidentAttachmentConflict, match="revision"):
            store.update_attachment(
                "alpha",
                created.attachment_id,
                expected_revision=1,
                incident_revision=incident["revision"],
                incident_sha256=incident["sha256"],
                document_revision=document["revision"],
                document_authority_sha256=document["authority_sha256"],
                sha256=document["content_sha256"],
                redaction_state="redacted",
                idempotency_key="stale-update",
            )


def test_expiry_revocation_delete_retention_and_purge(tmp_path, monkeypatch):
    store, clock, _principal, incident, document = _fixture_store(tmp_path, monkeypatch)
    with tenant_scope(tenant="alpha"):
        created = _create(store, incident, document)
        document["download_allowed"] = False
        assert store.authority_state(created) == "revoked"
        document["download_allowed"] = True
        clock[0] += 601
        assert store.authority_state(created) == "expired"

        deleted = store.delete_attachment(
            "alpha",
            created.attachment_id,
            expected_revision=created.revision,
            idempotency_key="delete-one",
        )
        assert deleted.status == "tombstoned"
        assert store.authority_state(deleted) == "tombstoned"
        assert store.list_for_incident("alpha", "privacy", incident["id"]) == ()
        assert store.list_for_incident(
            "alpha", "privacy", incident["id"], include_tombstones=True
        ) == (deleted,)
        assert store.retention_due("alpha") == ()

        clock[0] += 3_601
        assert store.retention_due("alpha") == (deleted,)
        assert store.purge_expired("alpha") == 1
        purged = store.get_attachment("alpha", created.attachment_id)
        assert purged.status == "purged"
        assert purged.safe_display_name is None
        assert purged.sha256 == document["content_sha256"]
        assert store.authority_state(purged) == "purged"

        active = _create(store, incident, document, key="create-retention-expiry")
        clock[0] += 3_601
        assert store.retention_due("alpha") == (active,)
        assert store.purge_expired("alpha") == 1
        assert store.get_attachment("alpha", active.attachment_id).status == "purged"


def test_projection_helper_requires_exact_response_revision(tmp_path, monkeypatch):
    store, _clock, _principal, incident, document = _fixture_store(
        tmp_path, monkeypatch
    )
    with tenant_scope(tenant="alpha"):
        created = _create(store, incident, document)
        response = iam.with_incident_attachment_metadata(
            {"id": incident["id"], "revision": incident["revision"], "status": "open"},
            store=store,
            tenant_id="alpha",
            incident_kind="privacy",
        )
        assert response["attachments"][0]["attachment_id"] == created.attachment_id
        assert response["attachments"][0]["authority_state"] == "active"
        assert response["attachment_metadata"]["incident_sha256"] == incident["sha256"]
        with pytest.raises(iam.IncidentAttachmentConflict, match="response revision"):
            iam.with_incident_attachment_metadata(
                {"id": incident["id"], "revision": 2},
                store=store,
                tenant_id="alpha",
                incident_kind="privacy",
            )


def test_history_tampering_is_withheld(tmp_path, monkeypatch):
    store, _clock, _principal, incident, document = _fixture_store(
        tmp_path, monkeypatch
    )
    with tenant_scope(tenant="alpha"):
        created = _create(store, incident, document)

        def tamper(raw):
            raw["history"][0]["event_sha256"] = "0" * 64

        store._records.update(  # noqa: SLF001
            created.attachment_id,
            tamper,
            expected_revision=created.revision,
            action="test_tamper",
            actor=created.principal_ref,
        )
        with pytest.raises(iam.IncidentAttachmentIntegrityError):
            store.get_attachment("alpha", created.attachment_id)
