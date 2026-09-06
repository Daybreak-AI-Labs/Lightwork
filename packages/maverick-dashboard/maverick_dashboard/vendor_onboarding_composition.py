"""Request-bound authority and existing connector composition for onboarding."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request
from maverick import config, connections
from maverick.governed_operations import ServiceContext
from maverick.paths import current_tenant_id_strict, tenant_scope
from maverick.vendor_onboarding_config import VendorOnboardingSettings

from .auth import caller_principal, require_permission


def settings() -> VendorOnboardingSettings:
    loaded = config.load_config()
    if config.config_source_errors(include_tenant=True):
        raise ValueError("vendor onboarding configuration is unavailable")
    return VendorOnboardingSettings.from_config(loaded)


def _context(request: Request) -> ServiceContext:
    require_permission(request, "operate")
    tenant = current_tenant_id_strict()
    if not tenant:
        raise HTTPException(409, "Select an active tenant")
    actor = caller_principal(request)
    if not actor:
        raise HTTPException(403, "A named authenticated reviewer is required")
    enrolled = settings()
    if not enrolled.enable:
        raise HTTPException(409, "Vendor onboarding is disabled")
    with tenant_scope(tenant=tenant):
        ticket = connections.get_connection(enrolled.ticket_connection)
    if not ticket:
        raise HTTPException(503, "Enroll the Jira connection for reviewed task delivery")
    authority = ticket.get("owner")
    if not authority:
        raise HTTPException(403, "Jira connection requires an enrolled authority")
    # Upload-only requests have no Drive dependency. The Drive adapter resolves
    # its named enrollment, same authority and grants at every search/read.
    return ServiceContext(
        tenant=tenant, actor=actor, resource_owner=enrolled.resource_owner,
        credential_authority=authority, credential_ref=enrolled.ticket_connection,
        document_credential_ref=enrolled.document_connection, human=True,
    )


@dataclass(frozen=True)
class WorkflowBinding:
    service: object
    context: ServiceContext


def bind_request(request: Request) -> WorkflowBinding:
    """Only trusted app composition may replace this factory for offline use."""
    from maverick.vendor_onboarding import VendorOnboardingService
    from maverick.vendor_onboarding_adapters import GoogleDriveEvidence, JiraTickets

    ctx = _context(request)

    def authorize(candidate: ServiceContext, _operation: str, _resource: str) -> bool:
        # Re-resolve current enrollment and RBAC at every sensitive boundary.
        # The service separately revalidates connection generations and sources.
        with tenant_scope(tenant=ctx.tenant):
            current = _context(request)
        return candidate.binding() == current.binding() and (not candidate.human or current.human)

    return WorkflowBinding(VendorOnboardingService(
        documents=GoogleDriveEvidence(), tickets=JiraTickets(), authorize=authorize,
    ), ctx)


def attach_vendor_onboarding_composition(app) -> None:
    if not hasattr(app.state, "vendor_onboarding_binding_factory"):
        app.state.vendor_onboarding_binding_factory = bind_request
