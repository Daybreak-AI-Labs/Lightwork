# Lightwork QA remediation ledger

This is the authoritative, test-backed ledger for the 2026-09-01 user-journey
audit and the follow-up security/continuity pass. It exists because no
`QA_FINDINGS.md` was present on `origin/main` or draft PR #2407 when remediation
began. A checked item means the implementation and its focused regression test
are both present on this branch; broad-suite and rendered-browser acceptance are
tracked separately at the end.

Status markers:

- `[x]` implemented with focused regression coverage on this branch
- `[~]` implementation is in progress or exists only on an unmerged branch
- `[ ]` not yet proven complete

## Tenant, identity, and deployment boundaries

- [~] **QA-001 — Compliance packet cache isolation (P1).** Cache identity must
  include the active tenant; a request for tenant B must never reuse tenant A's
  SOC 2/audit packet.
- [~] **QA-002 — Security register cache isolation (P1).** Threat-register cache
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
- [~] **QA-007 — Shared halt authorization (P1).** Arming and clearing the
  deployment-wide halt require global authority, with safe failure reporting.
- [~] **QA-008 — Model-cost-tier authorization (P1).** Deployment-wide routing
  economics cannot be changed by a tenant-local administrator.
- [~] **QA-009 — Savings-assumption authorization and precedence (P1).** Global
  assumptions require global authority, and the response/UI must report the
  effective value when a tenant override wins.
- [~] **QA-010 — Evaluation-corpus authorization (P1).** Accepting staged harness
  candidates into shared evaluation ground truth requires global authority.
- [x] **QA-011 — Tenant roster and usage confidentiality (P1).** Global tenant
  identity, plan, quota, goal, and spend views require global authority.
- [x] **QA-012 — Output-style authorization (P2).** Selecting a process-wide
  response style requires global authority.
- [~] **QA-013 — Partner Fleet SSRF defense (P1).** Saved tenant endpoints and
  every probe use the platform SSRF policy and reject loopback/private/link-local
  destinations unless an explicit trusted-estate policy admits them.
- [~] **QA-014 — Event-source host resource boundaries (P1).** IMAP/process-env,
  GitHub/process-token, and arbitrary-directory sources require the appropriate
  global authority and filesystem confinement.
- [ ] **QA-015 — User lockout prevention (P1).** Role changes/removals cannot
  demote the current last global administrator or remove the final recovery
  authority.
- [ ] **QA-016 — Department grant removal semantics (P1).** Removing a restricted
  grant must not silently broaden the principal to unrestricted access.
- [ ] **QA-017 — Tenant creation defaults (P1).** A new tenant cannot default to
  an unlimited `$0` quota without an explicit, accurately labeled choice.

## Credential custody and integration truthfulness

- [x] **QA-018 — Dashboard secret encryption (P1).** Provider keys, webhook
  signing material, and channel credentials are authenticated-encrypted before
  persistence and transparently resolved at runtime.
- [x] **QA-019 — MCP secret encryption (P1).** MCP bearer tokens, headers, stdio
  environment values, and OAuth values are encrypted in
  `runtime-overrides.toml` and opened only at runtime.
- [ ] **QA-020 — Partner Fleet credential custody (P1).** Stored partner bearer
  credentials use encrypted custody rather than plaintext JSON.
- [ ] **QA-021 — Provider rotation precedence (P1).** A dashboard key rotation
  cannot report success while an active tenant overlay continues using a stale
  key; the effective source and rotation target are explicit.
- [ ] **QA-022 — Connection update/rotation integrity (P1).** Rotating a token
  preserves connector, URL, ownership, access, and grants unless explicitly
  changed, and uses a revision guard.
- [ ] **QA-023 — Readiness proof (P1).** Provider, MCP, channel, plugin, and SaaS
  connection badges distinguish saved configuration from a successful bounded
  readiness check.
- [ ] **QA-024 — Plugin load truthfulness (P2).** Allowlisted/discovered packages
  are not shown as active until loading and tool discovery succeed.

## Setup, tenant activation, and navigation

- [x] **QA-025 — Installer path contract (P1).** `maverick init` honors
  `MAVERICK_CONFIG` and `MAVERICK_HOME`, writes and smoke-tests the same target,
  and never falls through to the real home unexpectedly.
- [ ] **QA-026 — First-run routing (P1).** An unconfigured installation enters a
  visible onboarding path rather than an enabled Chat action that fails later.
- [ ] **QA-027 — Provider-route consistency (P1).** Onboarding completion and
  goal preflight validate the provider/model route actually selected for the
  goal; an unrelated Codex login cannot make an Anthropic route appear ready.
- [~] **QA-028 — In-app active-tenant selection (P1).** Users can select an
  existing active tenant in-app; creation can optionally activate it; explicit
  request/env/client bindings retain documented precedence. Implemented on
  draft PR #2407 pending integration.
- [~] **QA-029 — Browser-friendly tenant gate (P2).** Tenant-bound workbenches
  render a recovery page for browser requests while API callers retain a
  machine-readable `409`. Implemented on draft PR #2407 pending integration.
- [~] **QA-030 — In-app module enablement (P1).** Disabled governance modules
  provide a functional, global-admin-gated activation control outside disabled
  fieldsets. Implemented across the self-service workbenches on draft PR #2407
  pending integration.
- [~] **QA-031 — Navigation decomposition (P2).** Privacy, Consent, AI oversight,
  and Third parties are grouped separately from core Governance. Implemented on
  draft PR #2407 pending integration; search and responsive acceptance remain.
- [ ] **QA-032 — Canonical goal links (P1).** External-agent run links target
  `/chat/goal/{id}`, not the nonexistent `/goals/{id}` route.
- [ ] **QA-033 — Consent discovery link method (P2).** Public-key discovery uses
  a valid GET URL before JavaScript enhancement and never exposes a `405` link.
- [ ] **QA-034 — Disabled gateway documentation (P2).** External-agent OpenAPI
  links are hidden or explained when the gateway runtime is disabled.
- [ ] **QA-035 — Unique route identities (P2).** Workbench routes have distinct
  names for reverse routing and observability rather than sharing one generic
  route name.

## State continuity and destructive interaction safety

- [~] **QA-036 — Continuous Assessment revision CAS (P1).** Review, re-assess,
  and evidence actions carry the displayed `expected_revision`; stale tabs get
  a conflict and refresh path instead of deciding unseen findings.
- [~] **QA-037 — Workflow Builder edit CAS (P1).** Empty/failed drafts preserve
  edit name, generation, and hidden limits; collision overwrite never drops the
  generation guard.
- [~] **QA-038 — Flow Designer canonical save URL (P1).** First save replaces
  the URL with `/flows/designer/{id}` so refresh reopens the saved flow.
- [~] **QA-039 — Flow draft recovery (P1).** Dirty state has an unload warning or
  recoverable local draft, including first-save and nested-flow navigation.
- [~] **QA-040 — Invalid Params preservation (P1).** Invalid JSON remains in the
  editor after validation, navigation, save, test, export, or chat attempts.
- [~] **QA-041 — Duplicate save serialization (P2).** Save is in-flight guarded;
  double-clicking cannot turn a successful save into a misleading conflict.
- [~] **QA-042 — Workflow type-switch continuity (P2).** Switching Template /
  Agent does not strand or destroy the current unsaved preview.
- [~] **QA-043 — Settings dirty-form protection (P2).** Saving one section warns
  about or preserves unsaved edits in every other Settings form.
- [ ] **QA-044 — Goal cancel recovery (P2).** A failed cancel request restores the
  button and reports the error; revoke/destructive actions require confirmation.
- [ ] **QA-045 — Attachment-panel recovery (P2).** A list failure does not hide
  the only Add File recovery action.
- [ ] **QA-046 — Flow webhook deletion integrity (P1).** Deletion requires
  confirmation and reports non-2xx responses rather than announcing success.
- [ ] **QA-047 — High-impact deployment confirmations (P2).** Learning-wide,
  workforce-team, Agent Factory, and similar deployment mutations require a
  clear confirmation and surface failure details.
- [ ] **QA-048 — Privacy decision recovery (P2).** Network rejection restores
  controls and retains the user's decision draft.

## Error handling, status truthfulness, and scale

- [ ] **QA-049 — Browser error presentation (P1).** Browser-facing 403/409/503
  failures render actionable HTML with navigation; APIs retain structured JSON.
- [ ] **QA-050 — Disabled-control integrity (P1).** Feature-disabled pages do not
  leave mutation controls keyboard/click enabled, including Data Use, Consent,
  evidence autopopulation, executive reports, continuous evidence, and external
  agent enrollment.
- [ ] **QA-051 — Global halt status integrity (P1).** Stop All never displays a
  false armed/cleared state after failed GET/POST/DELETE requests.
- [ ] **QA-052 — Permission-state truthfulness (P1).** Registered catalog tools
  are not called enabled/usable without effective provider, connection, feature,
  policy, and sandbox readiness.
- [ ] **QA-053 — Effective provider visibility (P1).** Environment-backed and
  tenant-selected providers appear consistently in Settings, Permissions, and
  the model resolver.
- [ ] **QA-054 — Re-enable truthfulness (P1).** Clearing a dashboard tool denial
  does not claim Enabled when base config still denies the tool.
- [ ] **QA-055 — Learning-state consistency (P1).** Hero counts, subsystem cards,
  and feature switches derive from one effective source and cannot report all
  systems on while their controlling features are off.
- [ ] **QA-056 — Workforce deployment truthfulness (P1).** Catalog specialists
  and connectors are labeled available, not “on staff” or “wired,” until
  deployed/configured; incomplete department metadata has intentional copy.
- [ ] **QA-057 — Permissions and Tools scale (P1).** Multi-thousand-row catalogs
  provide server-side search/filter/pagination or virtualization; responses do
  not render every control into one megabyte-scale page.
- [ ] **QA-058 — Settings information architecture (P2).** Settings has usable
  sections/tabs/search and does not present roughly 400 controls as one linear
  form wall.
- [ ] **QA-059 — Sidebar findability (P2).** Large navigation sets provide search
  and preserve usable desktop/mobile scrolling and labels.
- [ ] **QA-060 — Copilot context coverage (P2).** Every new module supplies a
  meaningful page context; providerless fallback links to the in-app setup path
  instead of instructing users to run a CLI command.
- [ ] **QA-061 — Localization honesty (P2).** Language options either translate
  the full navigation/actions/content contract or are explicitly labeled
  partial/preview.

## Downloads, links, and accessible interaction

- [~] **QA-062 — Unicode-safe downloads (P1).** Goal attachments and generated
  privacy documents use RFC 5987-compatible `Content-Disposition` and roundtrip
  non-Latin filenames.
- [ ] **QA-063 — Bulk download link semantics (P2).** Unavailable downloads are
  disabled buttons with an explanation, not bright `href="#"` no-op links.
- [~] **QA-064 — Flow Designer keyboard operation (P1).** Nodes, selection,
  connections, and canvas operations have documented keyboard alternatives and
  focus semantics.
- [~] **QA-065 — Flow control labels (P1).** Generated property, condition, and
  approval inputs have programmatic labels.
- [ ] **QA-066 — Modal focus containment (P1).** Workflow Builder dialogs trap
  focus, restore it on close, and support Escape.
- [ ] **QA-067 — Landmark and heading integrity (P2).** Pages avoid nested
  `<main>` landmarks, retain one H1, and do not skip heading levels.
- [ ] **QA-068 — Hidden upload focus integrity (P2).** Visually hidden chat/goal
  file inputs are not unexplained keyboard stops.
- [ ] **QA-069 — Accessible repeated actions (P2).** Repeated Deploy/Disable/etc.
  buttons include the target name in their accessible label.

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
- [ ] **QA-V05 — Merge integrity.** Draft PR #2407 and this remediation branch
  are reconciled without dropping either branch's tests or configuration
  serializer fields.
