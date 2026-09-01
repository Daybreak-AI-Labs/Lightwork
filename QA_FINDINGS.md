# Lightwork QA remediation ledger

This is the authoritative, test-backed ledger for the 2026-09-01 user-journey
audit and the follow-up security/continuity pass. It was created when
remediation began because the repository had no equivalent ledger. A checked
item means the implementation and its focused regression test
are both present on this branch; broad-suite and rendered-browser acceptance are
tracked separately at the end.

PR #2407 is merged. This branch includes the current `origin/main` generation
`f3042d94` through merge `de393c30`; the evidence below describes the
reconciled working tree rather than the old draft-PR snapshot.

Status markers:

- `[x]` implemented with focused regression coverage on this branch
- `[~]` implementation is in progress or exists only on an unmerged branch
- `[ ]` not yet proven complete

## Tenant, identity, and deployment boundaries

- [x] **QA-001 — Compliance packet cache isolation (P1).** Cache identity must
  include the active tenant; a request for tenant B must never reuse tenant A's
  SOC 2/audit packet.
- [x] **QA-002 — Security register cache isolation (P1).** Threat-register cache
  identity must include the active tenant and must not hide or disclose another
  tenant's findings.
- [x] **QA-003 — Billing owner isolation (P1).** Authenticated non-admin HTML and
  CSV billing exports use the same goal-owner boundary as `/api/v1/cost.csv`.
- [x] **QA-004 — MCP control-plane authorization (P1).** Listing, adding, and
  removing process-wide MCP definitions require a global admin, not a
  tenant-local admin.
- [x] **QA-005 — Channel control-plane authorization (P1).** Process-wide channel
  configuration and removal require a global admin.
- [x] **QA-006 — Plugin control-plane authorization (P1).** Plugin state and host
  installation require global authority; authorization is checked before host
  opt-in details are disclosed.
- [x] **QA-007 — Shared halt authorization (P1).** Arming and clearing the
  deployment-wide halt require global authority, with safe failure reporting.
- [x] **QA-008 — Model-cost-tier authorization (P1).** Deployment-wide routing
  economics cannot be changed by a tenant-local administrator.
- [x] **QA-009 — Savings-assumption authorization and precedence (P1).** Global
  assumptions require global authority, and the response/UI must report the
  effective value when a tenant override wins.
- [x] **QA-010 — Evaluation-corpus authorization (P1).** Accepting staged harness
  candidates into shared evaluation ground truth requires global authority.
- [x] **QA-011 — Tenant roster and usage confidentiality (P1).** Global tenant
  identity, plan, quota, goal, and spend views require global authority.
- [x] **QA-012 — Output-style authorization (P2).** Selecting a process-wide
  response style requires global authority.
- [x] **QA-013 — Partner Fleet SSRF defense (P1).** Saved tenant endpoints and
  every probe use the platform SSRF policy and reject loopback/private/link-local
  destinations unless an explicit trusted-estate policy admits them.
- [x] **QA-014 — Event-source host resource boundaries (P1).** IMAP/process-env,
  GitHub/process-token, and arbitrary-directory sources require the appropriate
  global authority and filesystem confinement.
- [x] **QA-015 — User lockout prevention (P1).** Role changes/removals cannot
  demote the current last global administrator or remove the final recovery
  authority.
- [x] **QA-016 — Department grant removal semantics (P1).** Removing a restricted
  grant must not silently broaden the principal to unrestricted access.
- [x] **QA-017 — Tenant creation defaults (P1).** A new tenant cannot default to
  an unlimited `$0` quota without an explicit, accurately labeled choice.

## Credential custody and integration truthfulness

- [x] **QA-018 — Dashboard secret encryption (P1).** Provider keys, webhook
  signing material, and channel credentials are authenticated-encrypted before
  persistence and transparently resolved at runtime.
- [x] **QA-019 — MCP secret encryption (P1).** MCP bearer tokens, headers, stdio
  environment values, and OAuth values are encrypted in
  `runtime-overrides.toml` and opened only at runtime.
- [x] **QA-020 — Partner Fleet credential custody (P1).** Stored partner bearer
  credentials use encrypted custody rather than plaintext JSON.
- [x] **QA-021 — Provider rotation precedence (P1).** A dashboard key rotation
  cannot report success while an active tenant overlay continues using a stale
  key; the effective source and rotation target are explicit.
- [x] **QA-022 — Connection update/rotation integrity (P1).** Rotating a token
  preserves connector, URL, ownership, access, and grants unless explicitly
  changed, and uses a revision guard.
- [x] **QA-023 — Readiness proof (P1).** Provider, MCP, channel, plugin, and SaaS
  connection badges distinguish saved configuration from a successful bounded
  readiness check.
- [x] **QA-024 — Plugin load truthfulness (P2).** Allowlisted/discovered packages
  are not shown as active until loading and tool discovery succeed.

## Setup, tenant activation, and navigation

- [x] **QA-025 — Installer path contract (P1).** `maverick init` honors
  `MAVERICK_CONFIG` and `MAVERICK_HOME`, writes and smoke-tests the same target,
  and never falls through to the real home unexpectedly.
- [x] **QA-026 — First-run routing (P1).** An unconfigured installation enters a
  visible onboarding path rather than an enabled Chat action that fails later.
- [x] **QA-027 — Provider-route consistency (P1).** Onboarding completion and
  goal preflight validate the provider/model route actually selected for the
  goal; an unrelated Codex login cannot make an Anthropic route appear ready.
- [x] **QA-028 — In-app active-tenant selection (P1).** Users can select an
  existing active tenant in-app; creation can optionally activate it; explicit
  request/env/client bindings retain documented precedence.
- [x] **QA-029 — Browser-friendly tenant gate (P2).** Tenant-bound workbenches
  render a recovery page for browser requests while API callers retain a
  machine-readable `409`.
- [x] **QA-030 — In-app module enablement (P1).** Disabled governance modules
  provide a functional, global-admin-gated activation control outside disabled
  fieldsets.
- [x] **QA-031 — Navigation decomposition (P2).** Privacy, Consent, AI oversight,
  and Third parties are grouped separately from core Governance.
- [x] **QA-032 — Canonical goal links (P1).** External-agent run links target
  `/chat/goal/{id}`, not the nonexistent `/goals/{id}` route.
- [x] **QA-033 — Consent discovery link method (P2).** Public-key discovery uses
  a valid GET URL before JavaScript enhancement and never exposes a `405` link.
- [x] **QA-034 — Disabled gateway documentation (P2).** External-agent OpenAPI
  links are hidden or explained when the gateway runtime is disabled.
- [x] **QA-035 — Unique route identities (P2).** Workbench routes have distinct
  names for reverse routing and observability rather than sharing one generic
  route name.

## State continuity and destructive interaction safety

- [x] **QA-036 — Continuous Assessment revision CAS (P1).** Review, re-assess,
  and evidence actions carry the displayed `expected_revision`; stale tabs get
  a conflict and refresh path instead of deciding unseen findings.
- [x] **QA-037 — Workflow Builder edit CAS (P1).** Empty/failed drafts preserve
  edit name, generation, and hidden limits; collision overwrite never drops the
  generation guard.
- [x] **QA-038 — Flow Designer canonical save URL (P1).** First save replaces
  the URL with `/flows/designer/{id}` so refresh reopens the saved flow.
- [x] **QA-039 — Flow draft recovery (P1).** Dirty state has an unload warning or
  recoverable local draft, including first-save and nested-flow navigation.
- [x] **QA-040 — Invalid Params preservation (P1).** Invalid JSON remains in the
  editor after validation, navigation, save, test, export, or chat attempts.
- [x] **QA-041 — Duplicate save serialization (P2).** Save is in-flight guarded;
  double-clicking cannot turn a successful save into a misleading conflict.
- [x] **QA-042 — Workflow type-switch continuity (P2).** Switching Template /
  Agent does not strand or destroy the current unsaved preview.
- [x] **QA-043 — Settings dirty-form protection (P2).** Saving one section warns
  about or preserves unsaved edits in every other Settings form.
- [x] **QA-044 — Goal cancel recovery (P2).** A failed cancel request restores the
  button and reports the error; revoke/destructive actions require confirmation.
- [x] **QA-045 — Attachment-panel recovery (P2).** A list failure does not hide
  the only Add File recovery action.
- [x] **QA-046 — Flow webhook deletion integrity (P1).** Deletion requires
  confirmation and reports non-2xx responses rather than announcing success.
- [x] **QA-047 — High-impact deployment confirmations (P2).** Learning-wide,
  workforce-team, Agent Factory, and similar deployment mutations require a
  clear confirmation and surface failure details.
- [x] **QA-048 — Privacy decision recovery (P2).** Network rejection restores
  controls and retains the user's decision draft.

## Error handling, status truthfulness, and scale

- [x] **QA-049 — Browser error presentation (P1).** Browser-facing 403/409/503
  failures render actionable HTML with navigation; APIs retain structured JSON.
- [x] **QA-050 — Disabled-control integrity (P1).** Feature-disabled pages do not
  leave mutation controls keyboard/click enabled, including Data Use, Consent,
  evidence autopopulation, executive reports, continuous evidence, and external
  agent enrollment.
- [x] **QA-051 — Global halt status integrity (P1).** Stop All never displays a
  false armed/cleared state after failed GET/POST/DELETE requests.
- [x] **QA-052 — Permission-state truthfulness (P1).** Registered catalog tools
  are not called enabled/usable without effective provider, connection, feature,
  policy, and sandbox readiness.
- [x] **QA-053 — Effective provider visibility (P1).** Environment-backed and
  tenant-selected providers appear consistently in Settings, Permissions, and
  the model resolver.
- [x] **QA-054 — Re-enable truthfulness (P1).** Clearing a dashboard tool denial
  does not claim Enabled when base config still denies the tool.
- [x] **QA-055 — Learning-state consistency (P1).** Hero counts, subsystem cards,
  and feature switches derive from one effective source and cannot report all
  systems on while their controlling features are off.
- [x] **QA-056 — Workforce deployment truthfulness (P1).** Catalog specialists
  and connectors are labeled available, not “on staff” or “wired,” until
  deployed/configured; incomplete department metadata has intentional copy.
- [x] **QA-057 — Permissions and Tools scale (P1).** Multi-thousand-row catalogs
  provide server-side search/filter/pagination or virtualization; responses do
  not render every control into one megabyte-scale page.
- [x] **QA-058 — Settings information architecture (P2).** Settings has usable
  sections/tabs/search and does not present roughly 400 controls as one linear
  form wall.
- [x] **QA-059 — Sidebar findability (P2).** Large navigation sets provide search
  and preserve usable desktop/mobile scrolling and labels.
- [x] **QA-060 — Copilot context coverage (P2).** Every new module supplies a
  meaningful page context; providerless fallback links to the in-app setup path
  instead of instructing users to run a CLI command.
- [x] **QA-061 — Localization honesty (P2).** Language options either translate
  the full navigation/actions/content contract or are explicitly labeled
  partial/preview.

## Downloads, links, and accessible interaction

- [x] **QA-062 — Unicode-safe downloads (P1).** Goal attachments and generated
  privacy documents use RFC 5987-compatible `Content-Disposition` and roundtrip
  non-Latin filenames.
- [x] **QA-063 — Bulk download link semantics (P2).** Unavailable downloads are
  disabled buttons with an explanation, not bright `href="#"` no-op links.
- [x] **QA-064 — Flow Designer keyboard operation (P1).** Nodes, selection,
  connections, and canvas operations have documented keyboard alternatives and
  focus semantics.
- [x] **QA-065 — Flow control labels (P1).** Generated property, condition, and
  approval inputs have programmatic labels.
- [x] **QA-066 — Modal focus containment (P1).** Workflow Builder dialogs trap
  focus, restore it on close, and support Escape.
- [x] **QA-067 — Landmark and heading integrity (P2).** Pages avoid nested
  `<main>` landmarks, retain one H1, and do not skip heading levels.
- [x] **QA-068 — Hidden upload focus integrity (P2).** Visually hidden chat/goal
  file inputs are not unexplained keyboard stops.
- [x] **QA-069 — Accessible repeated actions (P2).** Repeated Deploy/Disable/etc.
  buttons include the target name in their accessible label.

## Final continuity, authority, and recovery pass

- [x] **QA-070 — Global-control affordance truth (P1).** Emergency halt,
  learning, and sandbox controls render read-only without global authority,
  while tenant operators retain tenant-scoped tool admission.
- [x] **QA-071 — Destructive bulk/report safety (P1).** Consent bulk jobs and
  executive-report revoke/purge actions use target-specific confirmations,
  single-flight guards, opener-focus restoration, and control-state recovery.
- [x] **QA-072 — Async draft ownership (P1).** A completed Workflow Builder
  response remains attached to the Template/Agent mode that initiated it,
  cannot replace a newly selected mode, and does not scroll a hidden result
  into view.
- [x] **QA-073 — Assessment read-failure recovery (P1).** Register/detail load
  failures remain visible and actionable rather than silently producing an
  empty or stale interface.
- [x] **QA-074 — Partner loopback dual authority and revocation (P1).** Exact-IP
  loopback test estates require both global-admin authority and operator opt-in;
  every saved-row live probe rechecks that policy and fails before network I/O
  after opt-in is revoked.
- [x] **QA-075 — Connection-registry corruption containment (P1).** Only a
  missing registry means empty; malformed, unreadable, or unauthenticated
  ciphertext fails closed across list/create/test/delete, returns a recoverable
  `503`, and is never overwritten.
- [x] **QA-076 — Provider-inventory confidentiality (P1).** Raw provider and
  configuration-source inventory requires global authority, while authenticated
  viewers retain access to the redacted provider page.
- [x] **QA-077 — Verified provider rotation and exact rollback (P1).** Rotation
  binds to higher-authority configuration state, requires a real cross-process
  lock, verifies the effective credential after publication, and atomically
  restores the exact prior bytes and mode on conflict or failure.
- [x] **QA-078 — Deterministic history without false causality (P1).** Equal
  assessment timestamps have stable display/limit ordering, but risk direction
  is reported unknown when causal review order cannot be proven.
- [x] **QA-079 — Inclusive worker-lease expiry (P1).** A job at its exact lease
  deadline is reclaimable, while attempt fencing rejects a late stale worker.
- [x] **QA-080 — Workspace-picker continuity and redirect safety (P1).** The
  picker has one H1 and resumes the original or supplied same-origin path after
  selection; external, protocol-relative, backslash, control-character, and
  oversized targets are rejected.
- [x] **QA-081 — Version-compatible effective-route traversal (P1).** Route
  inspection supports both lazy route contexts and legacy included-router
  shapes without requiring a newer FastAPI helper.
- [x] **QA-082 — Workflow discoverability and naming coherence (P2).** Searching
  for “Workflow Builder” finds the visible GRC Studio destination, and the
  Workflow Builder document title matches its H1.
- [x] **QA-083 — Error-page heading integrity (P2).** Branded browser 404/500
  pages render exactly one primary heading without leaking exception detail.
- [x] **QA-084 — Learning-scope labeling (P2).** UI language distinguishes core
  learning components from separate feature switches and no longer implies one
  aggregate controls every learning system.

## Focused regression evidence

Each finding maps to at least one exact focused regression in the current
working tree.

- **QA-001** — `packages/maverick-dashboard/tests/test_control_plane.py::test_compliance_packet_cache_is_isolated_by_tenant`
- **QA-002** — `packages/maverick-dashboard/tests/test_api.py::TestSecurityRegister::test_security_register_cache_is_isolated_by_tenant`
- **QA-003** — `packages/maverick-dashboard/tests/test_billing.py::test_billing_is_owner_scoped_for_authenticated_viewer`
- **QA-004** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_mutate_deployment_control_plane`
- **QA-005** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_read_deployment_control_plane`
- **QA-006** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_plugin_install_authorizes_before_disclosing_opt_in_state`
- **QA-007** — `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::test_tenant_admin_cannot_toggle_shared_halt`
- **QA-008** — `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::test_tenant_admin_cannot_set_deployment_model_cost_tier`
- **QA-009** — `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::test_savings_reports_global_save_and_effective_tenant_override`
- **QA-010** — `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::test_tenant_admin_cannot_accept_shared_harness_ground_truth`
- **QA-011** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_read_deployment_control_plane`
- **QA-012** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_mutate_deployment_control_plane`
- **QA-013** — `packages/maverick-dashboard/tests/test_partner_endpoint_admission.py::test_partner_save_rejects_non_public_endpoints_by_default`
- **QA-014** — `packages/maverick-dashboard/tests/test_rbac.py::test_host_resource_event_sources_reject_tenant_local_admin`
- **QA-015** — `packages/maverick-dashboard/tests/test_rbac.py::test_final_global_admin_cannot_self_demote_or_remove`
- **QA-016** — `packages/maverick-dashboard/tests/test_suite_grants.py::test_remove_grant_only_uses_a_no_broader_default`
- **QA-017** — `packages/maverick-dashboard/tests/test_admin_tenants_api.py::test_unlimited_tenant_quota_requires_explicit_zero`
- **QA-018** — `packages/maverick-dashboard/tests/test_settings_store.py::test_concurrent_channel_and_provider_both_apply`
- **QA-019** — `packages/maverick-core/tests/test_mcp_overlay.py::test_add_then_read_stdio_server`
- **QA-020** — `packages/maverick-dashboard/tests/test_partner_store.py::test_partner_registry_is_encrypted_and_plaintext_never_persists`
- **QA-021** — `packages/maverick-dashboard/tests/test_settings_providers.py::test_tenant_provider_source_is_displayed_and_dashboard_rotation_refused`
- **QA-022** — `packages/maverick-dashboard/tests/test_connections_api.py::test_token_only_rotation_preserves_metadata_and_requires_fresh_revision`
- **QA-023** — `packages/maverick-dashboard/tests/test_integration_readiness_inventory.py::test_settings_renders_readiness_for_every_provider_row`
- **QA-024** — `packages/maverick-dashboard/tests/test_plugin_load_truth.py::test_successful_import_and_tool_contract_are_shown_loaded`
- **QA-025** — `apps/installer-cli/tests/test_wizard_fast_smoke.py::test_fast_setup_writes_and_smoke_reads_the_explicit_config`
- **QA-026** — `packages/maverick-dashboard/tests/test_get_started.py::test_unconfigured_root_enters_setup_and_chat_action_is_disabled`
- **QA-027** — `packages/maverick-dashboard/tests/test_get_started.py::test_unrelated_codex_login_does_not_complete_anthropic_route`
- **QA-028** — `packages/maverick-dashboard/tests/test_workspace_tenant.py::test_picker_quick_workspace_creates_selects_and_recovers_original_url`
- **QA-029** — `packages/maverick-dashboard/tests/test_workspace_tenant.py::test_browser_409_renders_the_picker_api_keeps_json`
- **QA-030** — `packages/maverick-dashboard/tests/test_feature_modules_api.py::test_new_workbench_gates_are_registered_and_configuration_is_separate`
- **QA-031** — `packages/maverick-dashboard/tests/test_ui_visibility.py::test_nav_groups_per_role`
- **QA-032** — `packages/maverick-dashboard/tests/test_route_identities.py::test_external_agent_run_links_use_canonical_goal_route`
- **QA-033** — `packages/maverick-dashboard/tests/test_consent_native_admin_api.py::test_public_receipt_keys_are_discoverable_without_ambient_authority`
- **QA-034** — `packages/maverick-dashboard/tests/test_external_agents_api.py::test_gateway_404s_while_disabled`
- **QA-035** — `packages/maverick-dashboard/tests/test_route_identities.py::test_all_application_route_names_are_unique`
- **QA-036** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_existing_assessment_mutations_require_revision`
- **QA-037** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_workflow_overwrite_requires_a_reviewed_generation`
- **QA-038** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_flow_designer_has_loss_and_double_submit_guards`
- **QA-039** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_flow_designer_has_loss_and_double_submit_guards`
- **QA-040** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_flow_designer_has_loss_and_double_submit_guards`
- **QA-041** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_flow_designer_has_loss_and_double_submit_guards`
- **QA-042** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_workflow_builder_preserves_context_and_traps_picker_focus`
- **QA-043** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_settings_warns_before_one_section_discards_another`
- **QA-044** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_goal_destructive_actions_and_attachment_failures_recover`
- **QA-045** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_goal_destructive_actions_and_attachment_failures_recover`
- **QA-046** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_webhook_deletion_is_confirmed_and_surfaces_server_failure`
- **QA-047** — `packages/maverick-dashboard/tests/test_qa_deployment_mutations.py::test_other_global_or_executable_deployments_have_the_same_safety_contract`
- **QA-048** — `packages/maverick-dashboard/tests/test_qa_continuity_ui.py::test_privacy_decision_network_failure_restores_the_draft_control`
- **QA-049** — `packages/maverick-dashboard/tests/test_error_pages.py::test_browser_http_errors_are_branded_and_actionable`
- **QA-050** — `packages/maverick-dashboard/tests/test_qa_disabled_control_integrity.py::test_continuous_evidence_and_external_enrollment_fail_closed`
- **QA-051** — `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::test_halt_shell_reports_failed_mutations`
- **QA-052** — `packages/maverick-dashboard/tests/test_tool_permission_truth.py::test_permissions_api_and_page_never_claim_blocked_tools_are_usable`
- **QA-053** — `packages/maverick-dashboard/tests/test_permissions.py::test_permissions_uses_same_env_provider_provenance_as_settings`
- **QA-054** — `packages/maverick-dashboard/tests/test_permissions.py::test_enable_reports_when_base_policy_still_denies_tool`
- **QA-055** — `packages/maverick-dashboard/tests/test_learning.py::test_feature_switches_use_live_subsystem_defaults`
- **QA-056** — `packages/maverick-dashboard/tests/test_workforce.py::test_workforce_distinguishes_catalog_from_deployment`; `packages/maverick-dashboard/tests/test_workforce.py::test_workforce_explains_missing_department_charter_without_overclaiming`
- **QA-057** — `packages/maverick-dashboard/tests/test_tools_pagination.py::test_tools_catalog_is_searched_and_paginated_before_render`
- **QA-058** — `packages/maverick-dashboard/tests/test_settings_page.py::test_settings_has_bounded_sections_keyboard_tabs_and_search`
- **QA-059** — `packages/maverick-dashboard/tests/test_sidebar_navfinder.py::test_page_finder_has_an_accessible_dialog_and_combobox_contract`
- **QA-060** — `packages/maverick-dashboard/tests/test_copilot_context.py::test_every_registered_dashboard_page_gets_page_specific_copilot_help`
- **QA-061** — `packages/maverick-dashboard/tests/test_i18n_fonts.py::test_language_selector_discloses_partial_translation_scope`
- **QA-062** — `packages/maverick-dashboard/tests/test_goal_attachments_download.py::test_download_unicode_filename_uses_rfc5987_header`
- **QA-063** — `packages/maverick-dashboard/tests/test_consent_bulk_jobs_page.py::test_download_starts_as_an_explained_disabled_button`
- **QA-064** — `packages/maverick-dashboard/tests/test_qa_flow_accessibility.py::test_qa_064_flow_designer_keyboard_selection_movement_and_pan`
- **QA-065** — `packages/maverick-dashboard/tests/test_qa_flow_accessibility.py::test_qa_065_generated_property_condition_and_approval_controls_are_named`
- **QA-066** — `packages/maverick-dashboard/tests/test_qa_flow_accessibility.py::test_qa_066_workflow_picker_traps_escape_tab_and_restores_opener`
- **QA-067** — `packages/maverick-dashboard/tests/test_qa_accessibility_cluster.py::test_extending_pages_leave_the_main_landmark_to_base`; `packages/maverick-dashboard/tests/test_error_pages.py::test_browser_500_has_a_single_primary_heading`; `packages/maverick-dashboard/tests/test_workspace_tenant.py::test_picker_honors_only_safe_local_return_targets`
- **QA-068** — `packages/maverick-dashboard/tests/test_chat_attachments.py::test_chat_page_has_attach_control`
- **QA-069** — `packages/maverick-dashboard/tests/test_qa_accessibility_cluster.py::test_repeated_actions_include_their_target`
- **QA-070** — `packages/maverick-dashboard/tests/test_ui_capability_recovery.py::test_tenant_operator_controls_do_not_leak_global_authority`
- **QA-071** — `packages/maverick-dashboard/tests/test_ui_capability_recovery.py::test_destructive_clients_confirm_exact_targets_and_recover_single_flight`
- **QA-072** — `packages/maverick-dashboard/tests/test_ui_capability_recovery.py::test_async_drafts_and_assessment_failures_stay_with_visible_context`
- **QA-073** — `packages/maverick-dashboard/tests/test_ui_capability_recovery.py::test_async_drafts_and_assessment_failures_stay_with_visible_context`
- **QA-074** — `packages/maverick-dashboard/tests/test_partner_endpoint_admission.py::test_loopback_request_cannot_enable_operator_policy`; `packages/maverick-dashboard/tests/test_partner_endpoint_admission.py::test_revoked_loopback_policy_blocks_every_live_probe_before_network`; `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_enroll_loopback_partner_even_when_operator_enabled`
- **QA-075** — `packages/maverick-core/tests/test_connections.py::test_corrupt_store_fails_closed_without_overwrite`; `packages/maverick-core/tests/test_connections.py::test_decryption_failure_fails_closed_without_overwrite`; `packages/maverick-dashboard/tests/test_connections_api.py::test_corrupt_connection_registry_returns_503_without_overwrite`
- **QA-076** — `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_tenant_admin_cannot_read_deployment_control_plane`; `packages/maverick-dashboard/tests/test_app_global_control_plane_authz.py::test_global_admin_can_read_deployment_control_plane_api`
- **QA-077** — `packages/maverick-dashboard/tests/test_settings_providers.py::test_provider_rotation_rollback_restores_exact_bytes_and_mode`; `packages/maverick-dashboard/tests/test_settings_providers.py::test_provider_rotation_refuses_process_local_lock_fallback`; `packages/maverick-dashboard/tests/test_settings_providers.py::test_provider_rotation_post_write_read_failure_rolls_back_with_503`
- **QA-078** — `packages/maverick-core/tests/test_assessment.py::test_equal_timestamp_history_has_canonical_order_and_limit`; `packages/maverick-dashboard/tests/test_privacy_program_depth.py::test_equal_timestamp_trend_does_not_infer_direction_from_random_ids`
- **QA-079** — `packages/maverick-core/tests/test_audit_concurrency_fixes.py::test_stale_complete_does_not_clobber_reclaimed_job`; `packages/maverick-core/tests/test_audit_concurrency_fixes.py::test_stale_fail_does_not_reschedule_reclaimed_job`
- **QA-080** — `packages/maverick-dashboard/tests/test_workspace_tenant.py::test_picker_honors_only_safe_local_return_targets`; `packages/maverick-dashboard/tests/test_workspace_tenant.py::test_picker_quick_workspace_creates_selects_and_recovers_original_url`
- **QA-081** — `packages/maverick-dashboard/tests/test_route_identities.py::test_compatible_route_traversal_recurses_without_fastapi_helper`; `packages/maverick-dashboard/tests/test_ui_visibility_fresh_process_smoke.py::test_every_registered_static_page_survives_fresh_html_process`
- **QA-082** — `packages/maverick-dashboard/tests/test_sidebar_navfinder.py::test_page_finder_accepts_the_workflow_name_for_the_grc_builder`; `packages/maverick-dashboard/tests/test_workflow_builder.py::test_workflow_builder_page_renders`
- **QA-083** — `packages/maverick-dashboard/tests/test_error_pages.py::test_404_html_for_browser`; `packages/maverick-dashboard/tests/test_error_pages.py::test_browser_500_has_a_single_primary_heading`
- **QA-084** — `packages/maverick-dashboard/tests/test_learning.py::test_learning_page_renders_the_moat`; `packages/maverick-dashboard/tests/test_learning.py::test_learning_page_has_enable_button`

## Verification gates

- [ ] **QA-V01 — Focused regression matrix.** Every checked item above names and
  passes at least one focused regression test.
- [ ] **QA-V02 — Dashboard/core/installer suites.** All affected package suites
  pass from the target checkout with `python -m pytest -q`.
- [ ] **QA-V03 — Static gates.** `python -m ruff check .`, `python -m vulture`,
  and `git diff --check` pass or have explicitly scoped, pre-existing failures.
- [ ] **QA-V04 — Rendered user journeys.** Fresh install, active-tenant switch,
  global versus tenant roles, stale tabs, failure recovery, keyboard-only use,
  and responsive layouts pass in the live dashboard without real provider
  spend.
- [ ] **QA-V05 — Merge integrity.** The merged PR #2407 content and this
  remediation branch are reconciled against the latest origin merge without
  dropping tests or configuration serializer fields.
