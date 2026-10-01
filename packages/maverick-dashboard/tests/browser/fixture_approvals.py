"""Synthetic pending approvals for the real Approvals page.

Only the disposable browser server imports this helper. No provider runs and
no action is executed: nothing consumes the decision.
"""
import time

from fastapi import HTTPException, Request
from maverick.approval_cards import build_approval_card, encode_approval_detail
from maverick.paths import tenant_scope
from maverick_dashboard.auth import caller_principal


def install(app, world, tenant, principal):
    def owner(request):
        if caller_principal(request) != principal:
            raise HTTPException(403, "Only the synthetic fixture owner can change fixture state.")

    @app.post("/__browser_fixture/approvals/setup", include_in_schema=False)
    async def setup(request: Request):
        owner(request)
        card = build_approval_card(
            action="salesforce.update_records",
            summary="Update 12 Salesforce account records",
            target_label="Salesforce: 12 account records",
            target={"object": "Account", "ids": ["001A", "001B"]},
            parameters={"field": "owner"},
            parameter_summary=["Change the owner of 12 accounts to Priya"],
            actor="user:browser-reviewer",
            workspace=tenant,
            role="operator",
            authority_versions={"role_revision": "role-v1"},
            input_versions={"crm_snapshot": "snap-1"},
            credential_versions={"salesforce_token_sha256": "a" * 64},
            budget={"currency": "USD", "maximum": 2.0},
            expires_at=time.time() + 3600,
            known_effects=["Account owners change in Salesforce."],
            unresolved_assumptions=[],
            before="The 12 accounts belong to Sam.",
            after="The 12 accounts belong to Priya.",
            irreversible=True,
        )
        with tenant_scope(tenant=tenant):
            approval_id = world.create_approval(
                "salesforce.update_records", risk="high", scope="Salesforce accounts",
                detail=encode_approval_detail(card), provenance="consent",
            )
        return {"synthetic": True, "approval_id": approval_id, "provider_calls": 0}
