"""Workspace-private onboarding bindings; deployment feature gates stay global."""
from __future__ import annotations

from dataclasses import asdict

from . import config, connections
from .governed_records import GovernedRecordStore, RecordConflict
from .paths import current_tenant_id_strict
from .vendor_onboarding_config import VendorOnboardingSettings

STORE = GovernedRecordStore("vendor_onboarding_setup", "VOS", "vendor_onboarding_setup")
RECORD_ID = "VOS-workspace"
REQUIRED_MODULES = ("connections", "vendor_onboarding", "vendor_management", "ai_vendor_risk", "model_risk_assurance", "evidence_graph")


def current():
    if not current_tenant_id_strict():
        raise PermissionError("Select a workspace before configuring vendor onboarding.")
    return STORE.get(RECORD_ID)


def settings(loaded: dict) -> VendorOnboardingSettings:
    row = current()
    if row is None:
        return VendorOnboardingSettings.from_config(loaded)
    raw = row.get("binding")
    if not isinstance(raw, dict):
        raise ValueError("Workspace vendor configuration is unavailable.")
    value = dict(raw, enable=(loaded.get("vendor_onboarding") or {}).get("enable") is True)
    return VendorOnboardingSettings.from_config({"vendor_onboarding": value})


def save(*, actor: str, ticket: str, document: str, ticket_revision: str,
         document_revision: str, expected_revision: int):
    if not actor.startswith("user:") or not current_tenant_id_strict():
        raise PermissionError("A named workspace administrator is required.")
    if not connections.enabled():
        raise PermissionError("Enable Connections before selecting credentials.")
    # Credential selection and commit share the exact lock used by enrollment,
    # rotation and deletion. Changing a credential later is separately checked
    # by the workflow at every sensitive boundary.
    with connections._mutation_lock():
        previous = current()
        if (previous or {}).get("revision", 0) != expected_revision:
            raise RecordConflict("Vendor setup changed. Reload before saving.")
        ticket_record = connections.get_connection(ticket)
        if (not ticket_record or ticket_record.get("owner") != actor
                or ticket_record.get("connector") != "jira"
                or ticket_record.get("revision") != ticket_revision
                or not ticket_record.get("token")):
            raise PermissionError("Select your current Jira connection with credentials.")
        if document:
            document_record = connections.get_connection(document)
            if (not document_record or document_record.get("owner") != actor
                    or document_record.get("connector") != "gdrive"
                    or document_record.get("revision") != document_revision
                    or not document_record.get("token")
                    or document_record.get("base_url") != "https://www.googleapis.com"):
                raise PermissionError("Select your current Google Drive connection with credentials.")
        if previous:
            resource_owner = previous["binding"]["resource_owner"]
        else:
            # Retain the owner of any deployment-configured historical cases.
            resource_owner = str((config.load_config().get("vendor_onboarding") or {}).get("resource_owner") or actor)
        binding = asdict(VendorOnboardingSettings.from_config({"vendor_onboarding": {
            "enable": True, "resource_owner": resource_owner,
            "ticket_connection": ticket, "document_connection": document or "unconfigured-drive",
        }}))
        # Require the same pinned-origin contract as production adapters. This
        # does not probe the provider or assert live readiness.
        from .governed_operations import ServiceContext
        from .vendor_onboarding_adapters import resolve_connection
        ctx = ServiceContext(tenant=current_tenant_id_strict(), actor=actor,
            resource_owner=resource_owner, credential_authority=actor,
            credential_ref=ticket, document_credential_ref=binding["document_connection"], human=True)
        resolve_connection(ctx, refresh=False)
        if document:
            resolve_connection(ctx, document=True, refresh=False)
        if previous:
            return STORE.update(RECORD_ID, lambda row: row.update(binding=binding),
                expected_revision=expected_revision, action="vendor_setup_changed", actor=actor)
        return STORE.create({"id": RECORD_ID, "binding": binding},
            action="vendor_setup_created", actor=actor, max_records=1)
