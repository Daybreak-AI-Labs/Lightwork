from pathlib import Path

p = Path('packages/maverick-core/maverick/recovery_authorities.py')
s = p.read_text(encoding='utf8')
s = s.replace('    "channel-poll-cursors": "channel_provider_poll_cursor_authority",', '''    "channel-poll-cursors": "channel_provider_poll_cursor_authority",
    # Customer workflow records can restore older grants, pending intent or
    # consumed operation receipts. They need current authority reconciliation;
    # an authenticated old backup is not evidence of current consent.
    "conversation_inbox": "conversation_intent_authority",
    "deliverable_bundles": "deliverable_release_authority",
    "project_workspaces": "project_workspace_membership_authority",
    "task_handoffs": "task_handoff_authority",
    "billing_sync_reviews": "billing_sync_review_authority",
    "budget_accounting_cases": "budget_reconciliation_authority",
    "employee_content_access": "private_content_grant_authority",
    "deliverable-share-releases": "public_release_authority",
    "employee_activation": "employee_activation_authority",
    "employee_departures": "employee_departure_authority",
    "permission_access_requests": "permission_request_authority",
    "personal_data_requests": "personal_erasure_request_authority",
    "owned_task_diagnostics": "private_diagnostic_release_authority",''')
anchor = '# These customer records are part of the supported application-level restore'
s = s.replace(anchor, '''# These governed records cannot authorize source work, change budget limits or
# grant source access. Preserve them as ordinary customer data. The explicit
# registry distinguishes this reviewed decision from an accidentally unmapped
# authority. Consumers still validate schema, owner and current source access.
NON_AUTHORIZING_GOVERNED_NAMESPACES = frozenset({
    "action_center_preferences", "personal_budget_warnings", "project_workspace_defaults",
})

# File authorities from customer workflows have the same anti-rollback needs
# as governed records. None currently has an intrinsic authenticated rebind
# transformation, so bootstrap retains the bytes and blocks admission.
CUSTOMER_FILE_AUTHORITY_POLICIES = {
    "employee_companions": "employee_companion_policy_authority",
    "personal-data": "personal_identity_link_authority",
    "personal-memory": "personal_memory_consent_authority",
    "personal-notifications": "personal_notification_delivery_authority",
    "device-pairing": "device_credential_authority",
    "model-tests": "model_test_dispatch_authority",
    "knowledge-reindex": "knowledge_reindex_execution_authority",
    "knowledge-task-context": "knowledge_task_binding_authority",
    "plugin-flow-pins": "plugin_execution_pin_authority",
    "plugin-task-pins": "plugin_execution_pin_authority",
    "plugin-workbench": "plugin_qualification_authority",
    "goal-share-previews": "external_share_dispatch_authority",
    "retention-runs": "retention_dispatch_authority",
}

''' + anchor)
s = s.replace('    if name.startswith("budget_receipts."):', '''    if name == "notification-deliveries.json":
        return "notification_dispatch_authority"
    if name.endswith(".ownership.json"):
        return "generated_content_owner_authority"
    if name.startswith("budget_receipts."):''')
s = s.replace('        **DYNAMIC_JSON_AUTHORITY_POLICIES,', '        **DYNAMIC_JSON_AUTHORITY_POLICIES,\n        **CUSTOMER_FILE_AUTHORITY_POLICIES,')
anchor = 'GLOBAL_START_BLOCKED_AUTHORITY_SAMPLES = {'
assert anchor in s
s = s.replace(anchor, anchor + '''
    "notification-deliveries.json": "notification_dispatch_authority",
    "generated_tools/example.ownership.json": "generated_tool_authority",
    "custom/example.ownership.json": "generated_content_owner_authority",
    **{f"{namespace}/record.enc": policy
       for namespace, policy in CUSTOMER_FILE_AUTHORITY_POLICIES.items()},''')
s = s.replace('    "APPLICATION_VALIDATED_JSON_AUTHORITY_POLICIES",',
              '    "APPLICATION_VALIDATED_JSON_AUTHORITY_POLICIES",\n    "CUSTOMER_FILE_AUTHORITY_POLICIES",\n    "NON_AUTHORIZING_GOVERNED_NAMESPACES",')
p.write_text(s, encoding='utf8', newline='\n')

p = Path('packages/maverick-core/tests/test_recovery_authority_registry.py')
s = p.read_text(encoding='utf8').replace('    GLOBAL_START_JSON_AUTHORITY_POLICIES,',
    '    GLOBAL_START_JSON_AUTHORITY_POLICIES,\n    NON_AUTHORIZING_GOVERNED_NAMESPACES,')
s = s.replace('packages / "maverick-channels" / "maverick_channels")',
    'packages / "maverick-channels" / "maverick_channels",\n                    packages / "maverick-dashboard" / "maverick_dashboard")')
s = s.replace('if authority_policy(relative, is_sqlite=False) is None',
    'if relative.split("/")[0] not in NON_AUTHORIZING_GOVERNED_NAMESPACES\n        and authority_policy(relative, is_sqlite=False) is None')
s = s.replace('    assert missing == {}', '''    assert missing == {}
    for namespace in NON_AUTHORIZING_GOVERNED_NAMESPACES:
        assert f"{namespace}/recovered.json" in declarations
        assert authority_policy(f"{namespace}/recovered.json", is_sqlite=False) is None''')
s = s.replace('        "f\'identity_governance_{sink}_requests\'",',
    '        "f\'identity_governance_{sink}_requests\'",\n        "\'conversation-organization-\' + context_for(request)",')
p.write_text(s, encoding='utf8', newline='\n')

p = Path('packages/maverick-core/tests/test_persistence_authority_inventory.py')
s = p.read_text(encoding='utf8').replace('from maverick.recovery_authorities import authority_policy',
    'from maverick.recovery_authorities import NON_AUTHORIZING_GOVERNED_NAMESPACES, authority_policy')
s = s.replace('                else:\n                    assert policy is not None',
    '                elif namespace in NON_AUTHORIZING_GOVERNED_NAMESPACES:\n'
    '                    assert policy is None\n'
    '                    assert entries[relative]["contract"] in {"preferences", "mixed_api"}\n'
    '                else:\n                    assert policy is not None')
p.write_text(s, encoding='utf8', newline='\n')
