# Lightwork QA remediation ledger

This is the authoritative, test-backed ledger for the 2026-09-01 user-journey
audit and the follow-up security/continuity pass. It was created when
remediation began because the repository had no equivalent ledger. A checked
item means the implementation and its focused regression test
are both present on this branch; broad-suite and rendered-browser acceptance are
tracked separately at the end.

PR #2407 is merged. The evidence below describes this branch's reconciled
working tree rather than the old draft-PR snapshot; the final fetch/base check
is recorded separately in **QA-V05** so this ledger does not freeze a stale SHA.

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

## Agent control, compliance, and portable-evidence pass

- [x] **QA-085 — Protected containment integrity (P1).** Security containment
  cannot be cleared or have its provenance repainted by ordinary resume/unrevoke;
  external-gateway contain/release operations verify persisted post-state and
  fail closed when the gateway cannot confirm it.
- [x] **QA-086 — Coherent bounded control-plane reads (P1).** Status and roster
  pagination derive from one bounded snapshot, dependency outages are sanitized
  `503` responses rather than healthy empty state, and concurrent attenuation is
  atomic, narrowing-only, and no-op safe.
- [x] **QA-087 — HALT authority and confidentiality (P1).** Deployment-local and
  shared HALT scope detection fails closed; dashboard mutations require
  deployment-global operate authority for local scope and global-admin authority
  for shared scope; inactive shared state is explicit; viewers cannot read raw
  incident reasons, actors, or absolute host paths.
- [x] **QA-088 — Agent-console response truthfulness (P1).** Protected conflicts
  are non-2xx structured responses, control-plane audit status is distinguished
  from component notes, stale/out-of-range pages recover sanely, native
  containment is counted, and incomplete cluster state disables controls without
  painting them enabled.
- [x] **QA-089 — Strict compliance-profile activation (P1).** Config and
  environment profiles use one strict resolver; malformed lists, blank entries,
  unknown names, or unreadable policy sources fail closed across governance,
  enterprise admission, encryption, and deployment floors instead of silently
  compiling a default-open posture. A genuinely absent/empty profile selection
  remains the supported no-profile state.
- [x] **QA-090 — Exact obligation-evidence provenance (P1).** Denial and
  admitted-dispatch evidence use the exact evaluated verdict profile snapshot;
  known-empty and unavailable provenance remain distinct; citations are
  deterministic, bounded, and disclose truncation.
- [x] **QA-091 — Complete tool lifecycle evidence (P1).** Agent and flow
  dispatches refuse to run when the mandatory call audit is unavailable; every
  post-dispatch success, tool error, exception, stop signal, or withheld output
  attempts exactly one correlated terminal result, with strict `ok`/`error`
  semantics and no blocked-output disclosure. Post-effect audit or processing
  uncertainty is quarantined as indeterminate and is not automatically retried.
- [x] **QA-092 — Trust-receipt correlation and privacy (P1).** Executed/failed
  claims require a genuine signed call/result pair with a recomputed shared
  commitment; unknown status cannot mean failure; the full call row and its
  `input_summary` are excluded by default and require an explicit bounded
  disclosure opt-in.
- [x] **QA-093 — Trust-root roles and temporal honesty (P1).** Receipt-envelope,
  historical audit-row, checkpoint, and anchor-ledger trust inputs are
  role-scoped without claiming that a deployment uses physically distinct keys.
  Envelope and checkpoint authority windows are evaluated at verification time;
  historical row and anchor-ledger windows intentionally use issuer-signed
  timestamps and disclose that limit. Mutable checkpoint files do not count as
  independent anchoring.
- [x] **QA-094 — Receipt publication and verifier recovery (P1).** Minting uses a
  stable bounded evidence snapshot and byte-exact, exclusive two-phase
  publication; it never clobbers a recipient file, preserves pending recovery
  state after incomplete completion evidence, and rejects oversized, malformed,
  raced, aliased, or invalid-UTF-8 inputs without a verifier crash.
- [x] **QA-095 — Regulatory setup discoverability (P2).** The installer always
  reaches the regulatory-profile choice independently of optional residency
  disclosure, and regulated guidance does not overclaim signed-audit readiness.

## Final principal, HALT, and console-integrity pass

- [x] **QA-096 — Exact runtime-principal binding (P1).** Native active work
  reports its real execution principal without replacing the human/resource
  owner; first execution-principal binding and run claiming are atomic; an
  explicit opaque capability principal is preserved exactly. An agent cannot
  adopt an unbound human-owned goal, and an unavailable or mismatched binding is
  refused before goal state, episode, event, model, trace, or dispatch side
  effects.
- [x] **QA-097 — Opaque-principal transport and storage (P1).** Bounded printable
  identity-provider subjects round-trip without trimming or path interpolation,
  use body/query transport instead of path segments, remain exact in
  execution-principal and revocation storage, and reject blank-padded or
  control-character aliases.
- [x] **QA-098 — Flow stop-signal terminal evidence (P1).** Cancellation and
  process stop signals attempt exactly one correlated, sanitized terminal result
  before the original signal propagates; a strict terminal-audit refusal does
  not replace or suppress that original signal.
- [x] **QA-099 — HALT transition and presentation truth (P1).** Caller-selected
  HALT parents use fail-closed private-directory validation; failed shared or
  local clear barriers retain or restore the durable stop without disarming the
  other authority out of order; the shared database row stays transactionally
  locked while the exact local generation is removed and an exact-owner process
  hold covers commit/rollback; generation-bound compare-and-delete preserves a
  concurrent re-arm and a degraded cross-process lock fails closed;
  authoritative shared attribution supersedes stale local detail; and
  post-effect HALT or principal-action audit refusal reports the applied state
  without leaking custody detail. The shell renders divergent local/shared state
  as partial and unverified, keeps controls disabled, and does not treat a `204`
  mutation as confirmed until a complete same-scope read succeeds.
- [x] **QA-100 — Bounded, ordered, accessible agent-console state (P1).** The
  client bounds declared, streamed, fallback, and stalled responses; globally
  orders concurrent ledger and mutation results; rejects malformed roster
  contracts before rendering controls; recomputes action eligibility; exposes
  stale health and table context to assistive technology; and does not steal
  unrelated focus during background failure.
- [x] **QA-101 — Historical trust-receipt compatibility (P1).** The verifier
  dispatches exact integer schema versions, preserves the immutable schema-v1
  claim contract for already-issued receipts, and applies current size,
  cryptographic, and safety checks without weakening schema-v2 semantics.
  Boolean, malformed, and unknown schema versions fail closed.
- [x] **QA-102 — Generation-bound protected containment (P1).** Protected
  containment rotates an opaque generation and releases only the exact observed
  generation under one strict cross-process authority lock. A concurrent newer
  containment wins, dependent trust/gateway/live-seal projections are cleared
  atomically or restored, and ordinary trust or gateway release paths refuse to
  bypass protected state. Legacy protected rows remain explicitly releasable by
  their empty compatibility generation.
- [x] **QA-103 — Atomic containment publication and projection (P1).** Protected
  generation publication, live sealing, and dependent trust/gateway restriction
  linearize under one stable per-principal strict authority and the registry
  writer lock. A concurrent release cannot delete the generation between those
  writes; incomplete projection remains restrictively contained and is never
  reported as a successful containment.

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
- **QA-085** — `packages/maverick-core/tests/test_revocation.py::test_protected_containment_requires_explicit_release`; `packages/maverick-core/tests/test_revocation.py::test_ordinary_revoke_cannot_repaint_protected_provenance`; `packages/maverick-core/tests/test_revocation.py::test_contain_racing_ordinary_unrevoke_never_loses_protection`; `packages/maverick-core/tests/test_agent_control_plane.py::test_external_contain_and_release_verify_gateway_post_state`; `packages/maverick-core/tests/test_agent_control_plane.py::test_external_gateway_containment_failure_is_not_reported_as_success`; `packages/maverick-core/tests/test_agent_control_plane.py::test_external_gateway_release_failure_retains_other_containment`
- **QA-086** — `packages/maverick-core/tests/test_agent_control_plane.py::test_snapshot_reuses_one_authority_scan_for_status_roster_and_page`; `packages/maverick-core/tests/test_agent_control_plane.py::test_native_goal_roster_scan_is_bounded`; `packages/maverick-core/tests/test_agent_control_plane.py::test_concurrent_attenuation_denies_union_without_lost_update`; `packages/maverick-core/tests/test_agent_control_plane.py::test_empty_or_unchanged_attenuation_does_not_write`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_dependency_outages_are_sanitized_503`
- **QA-087** — `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_scope_uncertainty_and_shared_status_outage_are_503`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_shared_halt_status_explicitly_confirms_inactive_cluster`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_local_halt_mutation_requires_global_operator`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_get_redacts_and_parses_incident_detail`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_get_refuses_a_multiply_linked_authority`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_post_atomically_replaces_alias_without_touching_referent`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_post_uses_private_atomic_publication`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_get_does_not_read_oversized_incident_detail`
- **QA-088** — `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_protected_resume_is_409_and_only_release_clears_it`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_fail_soft_audit_state_is_preserved_in_http_result`; `packages/maverick-dashboard/tests/js/agent_control_plane_client.test.js` cases `one combined snapshot drives status and roster and counts native containment`, `cluster halt status without an explicit cluster verdict stays disabled`, and `a stale second page recovers when the bounded roster becomes empty`
- **QA-089** — `packages/maverick-core/tests/test_compliance_profiles.py::test_configured_profiles_reject_malformed_or_unknown_policy`; `packages/maverick-core/tests/test_compliance_profiles.py::test_deployment_encryption_floor_uses_strict_shared_resolver`; `packages/maverick-core/tests/test_compliance_profiles.py::test_floor_consumers_fail_closed_when_policy_source_is_invalid`; `packages/maverick-core/tests/test_compliance_profiles.py::test_deployment_enterprise_admission_cannot_bypass_strict_profiles`; `packages/maverick-core/tests/test_governance.py::test_from_config_refuses_malformed_or_unknown_compliance_profiles`
- **QA-090** — `packages/maverick-core/tests/test_regulatory_compiler.py::test_evidence_can_use_the_exact_verdict_profile_snapshot`; `packages/maverick-core/tests/test_regulatory_compiler.py::test_denial_evidence_retains_all_applicable_citations_deterministically`; `packages/maverick-core/tests/test_regulatory_compiler.py::test_denial_evidence_is_bounded_and_reports_truncation`; `packages/maverick-core/tests/test_regulatory_compiler.py::test_governance_known_empty_profile_snapshot_returns_empty_tuple`; `packages/maverick-core/tests/test_regulatory_compiler.py::test_governance_policy_outage_returns_unavailable_profile_provenance`
- **QA-091** — `packages/maverick-core/tests/test_agent_loop.py::TestToolLifecycleAudit::{test_success_records_one_correlated_completed_result,test_tool_error_records_one_correlated_error_result,test_runtime_exception_records_one_correlated_result_then_propagates,test_cancellation_records_one_correlated_result_then_propagates,test_output_withheld_still_records_one_terminal_result,test_control_flow_stop_records_terminal_then_propagates,test_missing_call_audit_refuses_dispatch,test_missing_result_audit_withholds_completed_outcome,test_terminal_audit_refusal_after_effect_is_indeterminate,test_call_audit_refusal_propagates_before_effect,test_post_tool_hook_failure_preserves_completed_effect_status}`; `packages/maverick-core/tests/test_tool_authz.py::{test_missing_dispatch_audit_refuses_flow_before_tool_effect,test_result_audit_outage_is_indeterminate_and_never_retries,test_flow_control_stop_records_one_correlated_terminal_then_propagates,test_flow_control_stop_survives_terminal_audit_refusal,test_flow_success_pairs_attempt_with_executed_result,test_flow_failures_pair_attempt_with_failed_result}`
- **QA-092** — `packages/maverick-core/tests/test_trust_receipt.py::{test_tool_result_receipt_omits_call_row_by_default,test_execution_pair_is_an_explicit_bounded_disclosure,test_output_withheld_disposition_is_portable_claim_evidence,test_v2_pair_survives_real_writer_goal_resolution_and_anonymization,test_execution_pair_rejects_uncommitted_correlation,test_execution_pair_rejects_unknown_result_status,test_execution_pair_call_row_has_a_hard_size_limit}`
- **QA-093** — `packages/maverick-core/tests/test_trust_receipt.py::{test_historical_row_key_cannot_authorize_a_new_envelope,test_backdated_envelope_cannot_bypass_current_revocation,test_backdated_checkpoint_cannot_bypass_current_revocation,test_a_checkpoint_reaches_anchored_only_with_something_external,test_anchor_ledger_uses_audit_keys_not_checkpoint_keys,test_checkpoint_uses_checkpoint_keys_not_audit_keys}`
- **QA-094** — `packages/maverick-core/tests/test_trust_receipt.py::{test_export_surfaces_completion_audit_failure_as_recovery_state,test_export_preserves_recovery_marker_when_publish_raises_after_visibility,test_export_never_clobbers_an_existing_recipient_file,test_export_checks_the_final_serialized_byte_limit,test_receipt_file_read_is_bounded,test_receipt_file_with_invalid_utf8_fails_closed,test_receipt_file_symlink_or_reparse_alias_is_rejected,test_receipt_file_hardlink_is_rejected,test_receipt_identity_swap_during_read_is_rejected,test_build_uses_one_day_snapshot_for_verify_locate_and_pair,test_excessively_nested_suffix_is_a_failed_verdict_not_a_crash,test_wrapper_converts_unexpected_verifier_exception_to_closed_verdict}`
- **QA-095** — `apps/installer-cli/tests/test_wizard_regulatory.py::test_declining_residency_still_reaches_regulatory_picker`; `apps/installer-cli/tests/test_wizard_control_plane.py::test_the_wizard_asks_the_question`; `apps/installer-cli/tests/test_wizard_compliance.py::{test_compliance_guidance_does_not_overclaim_runtime_proof,test_panel_prints_only_for_a_regulated_deployment}`
- **QA-096** — `packages/maverick-core/tests/test_agent_control_plane.py::test_native_roster_uses_real_active_status_and_preserves_opaque_subjects`; `packages/maverick-core/tests/test_world_owner.py::{test_goal_execution_binding_is_durable_idempotent_and_mismatch_safe,test_goal_execution_principal_validation_is_at_the_store_boundary,test_concurrent_goal_execution_binding_has_one_winner,test_explicit_goal_selectors_preserve_both_principal_fields}`; `packages/maverick-core/tests/test_orchestrator.py::{test_goal_execution_principal_mismatch_refuses_before_side_effects,test_explicit_opaque_capability_principal_binds_owner_and_runs}`; `packages/maverick-core/tests/test_runner_identity.py::test_runner_refuses_mismatched_principal_before_setup_or_trace`; `packages/maverick-core/tests/test_grpc_service.py::{test_start_goal_preserves_distinct_owner_and_runtime_principal,test_run_goal_refuses_execution_principal_mismatch_before_claim,test_run_goal_preserves_human_owner_for_bound_agent_execution,test_per_agent_cannot_adopt_unbound_human_goal,test_run_goal_fails_closed_when_execution_principal_binding_is_unavailable,test_run_goal_claim_is_atomic_across_world_connections}`; `packages/maverick-dashboard/tests/test_fleet_dispatch.py::test_authenticated_owner_can_observe_real_principal_bound_fleet_run`; `packages/maverick-core/tests/test_postgres_tenant_migrations.py::{test_execution_binding_atomically_backfills_legacy_null_tenant,test_execution_binding_strict_tenant_cannot_claim_legacy_null,test_owner_aware_claim_uses_exact_tenant_compare_and_swap}`
- **QA-097** — `packages/maverick-core/tests/test_agent_control_plane.py::{test_opaque_principal_round_trips_without_becoming_a_path,test_principal_domain_rejects_blank_padded_or_control_text}`; `packages/maverick-core/tests/test_world_owner.py::test_goal_execution_binding_is_durable_idempotent_and_mismatch_safe`; `packages/maverick-core/tests/test_orchestrator.py::test_explicit_opaque_capability_principal_binds_owner_and_runs`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::{test_opaque_principal_uses_body_and_query_transport_end_to_end,test_opaque_principal_transport_rejects_ambiguous_values}`; `packages/maverick-core/tests/test_oidc_server.py::test_oidc_opaque_subject_is_bound_exactly_without_path_interpolation`; `packages/maverick-core/tests/test_world_tenancy.py::test_handle_message_keeps_opaque_principal_out_of_raw_tenant_paths`; `packages/maverick-dashboard/tests/js/agent_control_plane_client.test.js` case `opaque identity-provider subjects use body transport without canonicalization`
- **QA-098** — `packages/maverick-core/tests/test_tool_authz.py::{test_flow_control_stop_records_one_correlated_terminal_then_propagates,test_flow_control_stop_survives_terminal_audit_refusal}`
- **QA-099** — `packages/maverick-core/tests/test_agent_control_plane.py::{test_halt_uses_prepare_contract_for_caller_selected_parent,test_local_clear_barrier_failure_leaves_durable_stop,test_local_clear_preserves_a_generation_armed_while_waiting_for_lock,test_local_clear_preserves_a_concurrent_in_process_halt,test_local_clear_refuses_a_degraded_cross_process_lock}`; `packages/maverick-core/tests/test_cluster_killswitch.py::{test_world_halt_compare_and_delete_preserves_concurrent_rearm,test_world_halt_callback_failure_rolls_back_shared_clear,test_world_halt_mismatch_does_not_run_durable_clear,test_clear_transition_holds_are_exact_owner_and_preserve_concurrent_halt}`; `packages/maverick-core/tests/test_postgres_tenant_migrations.py::{test_halt_generation_migration_is_additive_and_last,test_postgres_halt_clear_is_generation_bound}`; `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::{test_halt_get_refuses_a_multiply_linked_authority,test_halt_clear_refuses_a_multiply_linked_authority,test_halt_post_atomically_replaces_alias_without_touching_referent,test_halt_post_uses_private_atomic_publication,test_shared_halt_arm_and_clear_record_cluster_scope,test_shared_halt_status_prefers_authoritative_shared_attribution,test_shared_clear_rearms_local_stop_when_cluster_clear_is_indeterminate,test_local_clear_does_not_delete_a_concurrent_newer_generation,test_shared_clear_compare_and_delete_preserves_a_concurrent_rearm,test_shared_clear_does_not_disarm_cluster_when_local_unlink_fails,test_strict_audit_refusal_reports_already_applied_halt_state,test_strict_audit_refusal_reports_already_applied_halt_clear,test_strict_audit_refusal_re_reads_applied_principal_state}`; `packages/maverick-dashboard/tests/test_qa_global_control_gates.py::{test_shared_halt_arm_failure_is_reported_and_keeps_local_stop,test_shared_halt_clear_failure_leaves_local_file_armed,test_halt_shell_authority_copy_is_scope_neutral}`; `packages/maverick-dashboard/tests/js/lightwork_ui_halt.test.js` cases `a local/shared disagreement is partial and never renders STOPPED`, `204 arm and clear responses stay unknown until a strict GET verifies state`, `a post-mutation GET from another scope cannot verify the change`, `failed, non-OK, and invalid polls invalidate a previously known state`, `an older overlapping poll cannot overwrite a newer verified result`, and `a stalled status read times out and invalidates known state`
- **QA-100** — `packages/maverick-dashboard/tests/test_agent_control_plane_page.py::test_agent_console_source_uses_bounded_governed_interactions`; `packages/maverick-dashboard/tests/js/agent_control_plane_client.test.js` cases `changing between mutation and ledger recomputes Apply eligibility`, `the latest concurrent ledger read owns the Last operation panel`, `a newer ledger result cannot be overwritten by an older mutation`, `malformed roster control fields fail closed before action rendering`, `a roster refresh error does not steal unrelated focus`, `refresh marks visible health stale until the snapshot settles`, `a stalled request times out and leaves controls fail closed`, `fallback response limits count UTF-8 bytes rather than JS characters`, `a declared oversized response is cancelled before body allocation`, `a chunked response is cancelled as soon as its byte budget is exceeded`, and `timeout aborts a response whose streamed body stalls`
- **QA-101** — `packages/maverick-core/tests/test_trust_receipt.py::{test_a_valid_historical_v1_receipt_verifies_with_v1_claim_semantics,test_malformed_and_unknown_receipt_versions_fail_closed}`
- **QA-102** — `packages/maverick-core/tests/test_revocation.py::{test_stale_protected_release_preserves_new_generation_and_skips_callback,test_protected_release_refuses_degraded_cross_process_lock,test_legacy_protected_record_can_be_released_by_its_empty_generation}`; `packages/maverick-core/tests/test_agent_control_plane.py::{test_stale_release_cannot_clear_a_newer_external_containment,test_trust_cli_restore_linearizes_before_concurrent_containment}`; `packages/maverick-core/tests/test_cli_external_agents.py::test_ordinary_restore_and_gateway_release_refuse_security_containment`; `packages/maverick-dashboard/tests/test_external_agents_api.py::test_ordinary_admin_restore_and_release_refuse_security_containment`; `packages/maverick-core/tests/test_external_agents.py::test_admin_containment_and_release_verify_persisted_gateway_state`
- **QA-103** — `packages/maverick-core/tests/test_agent_control_plane.py::test_release_cannot_delete_generation_between_containment_projections`; `packages/maverick-core/tests/test_agent_edr.py::test_revocation_is_confirmed_rather_than_assumed`

- **QA-099 live Postgres transaction** — `packages/maverick-core/tests/test_postgres_world.py::test_halt_durable_clear_failure_rolls_back_locked_generation`
- **QA-099 cached-state arm** — `packages/maverick-dashboard/tests/test_agent_control_plane_api.py::test_halt_post_invalidates_a_cached_local_clear`

## Verification gates

- [x] **QA-V01 — Focused regression matrix.** Every checked item above names and
  passes at least one focused regression test.
- [x] **QA-V02 — Dashboard/core/installer suites.** All affected package suites
  passed from the target checkout. To avoid repeating multi-thousand-test runs,
  the completed broad runs were reconciled with exact reruns of every observed
  failure: core `19,539 passed, 269 skipped` plus five corrected nodes;
  dashboard `3,968 passed, 1 skipped` plus one corrected node; installer `356
  passed`; and dashboard JavaScript `189 passed`.
- [x] **QA-V03 — Static gates.** `python -m ruff check .`, `python -m vulture`,
  and `git diff --check` pass or have explicitly scoped, pre-existing failures.
- [ ] **QA-V04 — Rendered user journeys.** Fresh install, active-tenant switch,
  global versus tenant roles, stale tabs, failure recovery, keyboard-only use,
  and responsive layouts pass in the live dashboard without real provider
  spend. Fresh-workspace setup, two-tenant switching, desktop/mobile layouts,
  keyboard navigation, page-specific Copilot, opaque principals, HALT arm/clear,
  and live server-outage recovery were exercised against an isolated local
  dashboard. Global-versus-tenant role rendering and an unsafe stale-tab
  mutation remain unverified because the isolated modules were fail-closed and
  no alternate live identity provider was configured.
- [x] **QA-V05 — Merge integrity.** The merged PR #2407 content and this
  remediation branch are reconciled against the latest origin merge without
  dropping tests or configuration serializer fields. After a fresh fetch,
  `origin/main` is the branch merge-base, the branch is 16 commits ahead and
  zero behind before this handoff commit, and PR #2407 merge commit
  `34472745f91a7fcb7e51850388b5b187bcd9ac23` is an ancestor of `HEAD`.
