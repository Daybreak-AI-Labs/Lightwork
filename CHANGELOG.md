# Changelog

All notable changes to Lightwork. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **OpenTelemetry metrics and request spans** (opt-in). `MAVERICK_OTEL_METRICS=1`
  (or `[observability] otlp_metrics`, on the All settings page) also sends the
  Prometheus series over OTLP to the same collector, under the same names and
  labels, at `/v1/metrics` beside the trace endpoint. With OTLP tracing on, each
  dashboard HTTP request is one server span named by route template (for example
  `HTTP GET /goals/{goal_id}`). FastAPI's own per-request span, which records the
  raw path and query, is switched off in that mode.
- **Chat threads** — a chat is now a conversation, not a single goal. The
  first message opens a thread; every later message from the page's composer
  becomes a follow-up goal (`POST /chat/goal/{id}/reply`) that runs inside
  the thread's world conversation, so the agent sees the earlier messages and
  answers as prior turns. The goal page shows the whole thread (earlier
  questions as bubbles, earlier answers as prose with a link to the work
  behind each), keeps one composer pinned at the bottom that answers an open
  question or sends the next message (Enter sends, Shift+Enter breaks a
  line, attachments and dictation inline, Stop while Lightwork works). The
  chat pages now render in their own shell (`chat_base.html`): a chat list on
  the left (New chat, Search chats, Dashboard, Recents, account), a clean
  canvas with no top bar, product navigation or footer, and the composer
  docked at the bottom, the way a chat client lays itself out. The stopped-run
  notice is a quiet "Try again" with the spending limit tucked away, and a
  finished answer carries thumbs, Copy, Read aloud and Edit with sharing,
  task files and project under More.
- **Goal page reads as a conversation** — the work log is a list of steps in
  plain words (what was planned, which tool was used, what was found) instead
  of a monospace `[agent] kind` trace; when the evidence gateway withholds
  model-derived events, the page folds every placeholder into one progress
  line ("13 steps completed…") rather than repeating the sentence once per
  step. The result and any prose deliverable render as formatted text
  (headings, lists, tables, code, links) through a new dependency-free,
  DOM-only renderer at `/static/lightwork-markdown.js`; raw HTML and
  `javascript:` links in model output stay inert text. The status badge
  reads *queued / working / paused / done*, and the chat landing opens on a
  greeting and the composer instead of a hero panel.
- **Workspaces page edits the daily spend limit** — each workspace row now
  carries its limit as an editable field (0 for no daily limit) saved through
  the existing `POST /api/v1/admin/tenants/{id}/quota`, instead of a value that
  could only be set at creation. The row explains that physical provider
  attempts reserve projected spend against the current allowance.
- **Users page assigns members to any workspace** — the Members section gets a
  workspace picker (every active workspace for a global administrator, the
  member's own workspaces otherwise), so assigning someone to a workspace no
  longer requires first switching your own selected workspace. People who
  arrived through an accepted invite are shown by the email the invite was sent
  to, with the sign-in identity as the detail, on the roster, in the member
  list and in the colleague picker.
- **Settings offers the models your keys can reach** — a new
  `maverick.model_discovery` module asks each configured provider's own
  model-list endpoint (Anthropic, OpenAI, Gemini, xAI, DeepSeek, Moonshot,
  OpenRouter, Ollama and OpenAI-compatible self-hosted servers) through the
  SSRF-safe client, keeps well-formed ids only, and caches the answer under
  the data home. Settings > Models lists those ids next to the shipped
  catalog, refreshes them on demand or whenever a key is saved or tested, and
  shows each provider's list status. `[models] discovery` (default on, wizard
  question) and `MAVERICK_MODEL_DISCOVERY=0` switch it off;
  `discovery_ttl_minutes` sets freshness.
- **Tools and Connections say what Enterprise mode refuses** — while
  Enterprise mode is on, a signed-in person's task cannot run the
  hand-written connector tools that read server-wide environment credentials
  (Salesforce, ServiceNow, Workday, SAP, Jira, Slack and more than sixty
  others).
  The Tools page now marks each such tool "Not available in Enterprise mode
  yet" and counts them, and the Connections page warns that a saved connection
  does not unlock them, so an administrator learns this before rollout instead
  of from a refused task. `maverick.tools.enterprise_unavailable_tool_names()`
  states the same rule the registry applies at call time. Moving those
  connectors onto saved connections is not part of this change.

### Changed
- **Ordinary tasks have optional limits** — omitted dollar, time, token,
  tool-call and step limits remain unset across customer entrypoints. Existing
  numeric settings stay intact; Settings can reversibly disable saved task
  defaults. Explicit invocation limits and organization controls still apply.
  Metered workflow agents retain actual usage and result text; an allowance
  refusal keeps unfinished work available for an authorized retry. Durable
  workflow frames do not enable ordinary-agent checkpoints or promise replay
  of uncertain external effects. See [task limits](docs/task-limits.md).
- **Organization admission follows physical provider attempts** — concurrent
  retries and hedges retain durable projected holds, known costs settle against
  cumulative usage receipts, and unknown charges never expire automatically.
  Late receipt corrections preserve prior nonreceipt spending; absorbed child
  scopes avoid duplicate charges. Released scopes move to indexed history
  while pending scopes remain bounded and fail closed on unreadable state.
- **Pack tool contracts match their declared capabilities** — read-only SQL
  and spreadsheet tools expose only read operations, and deployment refuses
  unavailable required workflow tools after custom adapters are assembled.
  Built-in skill instructions use real tool contracts. Risk ceilings and
  denylists are preserved; 255 reviewed packs still require named adapters or
  explicit approved-export preparation before deployment.
- **Governance workbenches lose their plumbing** — across 32 governance,
  consent, privacy, data-use, AI-oversight and third-party pages the request
  references and idempotency keys are generated automatically and no longer
  shown, the "Technical details" disclosures and raw status documents are
  hidden, and the pages stay on the sidebar and the compliance landing. Only
  the Research lab is held back (`ready: False` in the page registry keeps a
  page governed and reachable by URL but off every customer surface).
- **The first administrator gets a workspace, not a dead end** — a signed-in
  global administrator who reaches a tenant gate on an installation with no
  workspace has the one-click `local` workspace created and selected and is
  sent back to the page, instead of "No workspaces exist yet".
- **Settings > Models is grouped by provider** — the default and per-role
  pickers are real selects grouped by configured provider (allow-listed models
  stay offered before their key is saved) with a "Type a model id" option
  instead of free-text fields with a hidden datalist, the allow-list
  is grouped per provider with select-all/clear and a live/catalog marker,
  and an overview shows how many providers, models and live lists are in
  play. Provider rows under Provider keys show how many models each key
  reaches and offer a per-provider refresh.
- **Bedrock through the Anthropic SDK** — `BedrockClient` now builds on
  `AnthropicClient` using the SDK's Bedrock clients (Claude in Amazon Bedrock
  by default, the legacy InvokeModel endpoint on request), so prompt caching,
  adaptive thinking, thinking signatures and `tool_choice` work on Bedrock.
  IAM/SigV4 auth from the AWS credential chain is the default (new `bedrock`
  extra); a bearer token is used when `BEDROCK_API_KEY` or
  `[providers.bedrock] auth_mode = "api_key"` is set. Bedrock ids map onto the
  Claude API rate card for planning estimates only; live accounting under
  strict pricing refuses with the remedy named (AWS rates are not verified).
- **`tool_choice`, strict tools and serialised tool calls** reach the
  providers: `LLM.complete`/`complete_async` accept `tool_choice` in Anthropic
  vocabulary (translated to OpenAI `tool_choice`/`parallel_tool_calls`), a
  `Tool.strict` flag emits `strict: true` on opt-in only, and a forced choice
  is clamped to `auto` plus an instruction where the API rejects it (Claude
  Fable/Mythos 5.1, manual extended thinking).
- **Per-turn blackboard delta** — each tool-running turn appends what other,
  unsealed agents posted since the agent's last turn (bounded by
  `MAVERICK_BB_TURN_DELTA_ENTRIES`), so a running orchestrator sees fan-out
  caps, seals and sibling findings instead of only its first-turn brief.
- **Dashboard compression and fingerprinted shell assets** — gzip behind
  `[dashboard] compress` (default on, wizard question) and content-hashed
  `/static/*?v=` links with a one-year immutable cache for the shell CSS/JS.
- **`/compartments` search and pagination** (`q`, `page`, `per_page`), like
  `/tools`, instead of one multi-megabyte page.

### Fixed
- **Client and consent state stays attached to the current action** — stale
  assessment and demo responses no longer overwrite a newer selection; failed
  saves remain visible. Consent edits carry their version and failed bulk
  preference updates preserve the intended choices for retry. Polling clients
  handle terminal failures, deadlines and changed identities; Unicode titles
  truncate safely. The Bedrock external-agent example uses the server's current
  object and operation contracts. These changes do not imply native installed
  client or live external-provider acceptance.
- **Setup and deployment retain the selected workspace** — local setup checks
  the workdir, VPS services use the configured path, container defaults align
  with persistent mounts, and SSH keeps its remote workdir explicit. Config
  publication uses private atomic replacements with ordinary-failure rollback;
  the config/secret pair is not a crash-atomic transaction. Deployment recipes
  include required dashboard credentials and bounded readiness/relay checks
  without treating a demo response as production acceptance.
- **A goal is no longer withheld because the evidence policy was never
  created.** With the AI evidence gateway enabled, every goal in a tenant that
  had not published a `default` policy stopped with "AI evidence gateway
  withheld the generated output. Operator action is required: AI evidence
  policy 'default' does not exist", and a per-user isolation tenant or a
  session-selected workspace had no way to create one through the static
  dashboard token (every policy call answered 503). The gateway now provisions
  the tenant's `default` policy from its built-in defaults on the first
  delivery, as an audited `policy_created` mutation, so the answer is receipted
  and disclosed instead of blocked; an explicit non-default policy id must
  still exist.
- **"Try again" and goal creation honour the deployment's spend cap.** The
  former fixed fallback could refuse a requested limit below the actual
  Settings cap. Resume, REST goal creation, compose, signed webhooks, cost
  projection and halt guidance now resolve the active optional task policy.
  An omitted limit stays unset; a configured limit is shown in the refusal
  message. The task-limits switch controls saved defaults without disabling
  explicit invocation or organization limits.
- **Rust MCP client example moves off a vulnerable `rmcp`.** The example
  pinned `rmcp 1.7.0`, which OSV now flags for GHSA-33f5-2c5q-wgwj
  (CVE-2026-63127, OAuth protected-resource metadata not validated) and
  GHSA-9pj6-vhgr-3mwh (CVE-2026-63128, unauthenticated session-table leak in
  the Streamable HTTP server transport); the `audit` CI job fails on any
  unfiltered advisory. The example and the quickstart now pin `rmcp 2.0.0`,
  the first fixed release; the client code is unchanged and still drives
  `maverick mcp` end-to-end. Neither advisory reached Lightwork itself: the
  example only uses the stdio client transport, and no shipped package
  depends on `rmcp`.
- **Answering the agent's question continues the run** — a goal parked by
  `ask_user` sat `blocked` after the answer was recorded until someone found
  "Resume this goal". `POST /api/v1/goals/{id}/answer` now re-queues the run
  (same admission gate and execution identity as Resume) once every open
  question has an answer, and the page keeps following the live stream with
  the question and your reply kept in the conversation.
- **Free OpenRouter models run under strict pricing.** OpenRouter's model
  listing publishes a price per model; a model it lists at $0 for prompt and
  completion (its `:free` and stealth preview models) was still refused as an
  "unpriced model" because the rate card had no row for it. Model discovery
  now records the ids OpenRouter advertises at zero, and the budget prices them
  at $0 through a tracked evidence entry that cites the provider listing, so
  such a run starts instead of stopping before its first call. Any non-zero or
  unknown OpenRouter price still fails closed as before.
- **A sealed value in the dashboard Settings overlay no longer stops the
  process from starting.** Once Settings had encrypted a provider key into
  `dashboard-config.toml`, the next boot died before listening: the first
  `data_dir()` call resolved the tenant, which loaded config, which opened the
  overlay, which imported `crypto_at_rest` -- whose import-time key-path
  binding called `data_dir()` again and hit its own half-initialised module
  (`cannot import name 'deployment_key_scope' ... circular import`). The
  key path is now resolved when used, never at import, and config loading
  will not re-enter its own unseal (a nested load during decryption sees the
  tokens unopened; it only ever needs plaintext keys such as `[client] id`).
  Both the running image and the previous one were affected; the fault was
  latent from the day the overlay learned to seal values and only a restart
  with a sealed value present triggered it.
- Wizard-generated Kubernetes sandbox could not run a command: the backend now
  verifies a deny-all NetworkPolicy and runs under it (fails closed with the
  manifest when none exists); the wizard asks for the namespace and networking.
- VPS bootstrap installs the same `postgres`, `oidc` and `s3` extras as the
  container image (`MAVERICK_CORE_EXTRAS`).
- Malformed provider usage is a `UsageAccountingError` (a provider fault),
  no longer reported as "budget exceeded"; a review-checkpoint stop is
  reported as a reviewer decision via the new `AgentResult.stop_kind`, not as
  "a HALT file is present".
- Tenant over-quota gate counts in-flight reservations, so a run whose
  allowance is already held is refused before it starts instead of dying on
  its first model call with a $0 cap.
- Oversight "Active now" table updates rows in place (keyboard focus and
  screen-reader position survive the 5-second refresh); artifact version
  history recovers from a failed fetch and retries.
- `<html lang>` declares the language the page body is written in; the
  translated chrome carries its own `lang`.
- Executive PDF paginates by vertical space, so no line lands in the footer.
- OneTrust vendor import reports every skipped row with its reason
  (duplicate, invalid, capacity) in the API, CLI and privacy page.
- Marketing page positions Lightwork as the governed AI workforce platform
  with the Daybreak favicon and licensing-appropriate calls to action.
- **Per-tenant BYOK provider keys** — a config-gated, default-off layer
  (`[providers] per_tenant_keys` / `MAVERICK_PER_TENANT_KEYS`) that lets an
  active tenant run on ITS OWN LLM/provider API key instead of the
  deployment-global one. Opt-in is per tenant (a `byok` flag on the tenant
  record via `tenant.registry.set_byok` — a flag only, never the key bytes); the
  key is resolved through the existing secret provider under a tenant-scoped name
  (`MAVERICK_TENANT_<digest>_<PROVIDER>_API_KEY`, the digest derived from the
  tenant's collision-safe namespace key so two tenants can never alias one name;
  `config.tenant_provider_key_env` reports the exact name). Threaded into
  `llm._provider_api_key` where the provider client is constructed, so an agent
  running under a tenant uses that tenant's key. **Fail-safe:** no active tenant,
  the knob off, the tenant flag off, or a missing key all fall back to the
  deployment-global key byte-for-byte — a BYOK misconfiguration never takes an
  agent offline — and the key bytes never reach config, the repo, the tenant
  record, or logs (the name ends in `_API_KEY` so the GitHub Action output
  sanitizer redacts it). Installer wizard step + docs. BYOK only — no Bedrock
  resale, usage metering, or markup (that waits on a separate pricing decision).
  Distinct from per-tenant `[kms]` BYOK (envelope-encryption key custody).
- **Stripe billing collection** — a config-gated, default-off integration
  (`maverick.billing_stripe`; `[billing.stripe] enable` / `MAVERICK_STRIPE_ENABLE`)
  that turns the metered-usage invoices from `maverick.billing` into real charges
  on the platform's own Stripe account. One Stripe **customer per tenant**
  (create-or-lookup, idempotent; the `cus_...` id is stored on the tenant record),
  a **subscription** matching the tenant's plan (the recurring platform fee, priced
  from `[billing.stripe.prices]`), and the metered invoice pushed as Stripe invoice
  items behind the `bill_metered_usage` **pricing-decision flag** — off by default,
  so only the platform fee is billed until the model-usage markup is decided (no
  markup is hardcoded; amounts come from the `RateCard` that rated the invoice). A
  signature-verified **webhook** (`MAVERICK_STRIPE_WEBHOOK_SECRET`, stdlib HMAC —
  no SDK needed) maps `invoice.payment_failed` / a past-due subscription to a
  downgrade to the base entitlement tier (via a new `billing_status` on the tenant
  record, honored by `billing.tenant_entitled`/`feature_allowed`) and
  `payment_succeeded` / an active subscription back to a restore — the subscribed
  plan is never lost, so restore is exact. New operator commands `maverick billing
  stripe-status`, `stripe-preview <tenant>` (offline dry-run) and `stripe-sync
  <tenant>`, plus an installer wizard step. The secret key
  (`MAVERICK_STRIPE_SECRET_KEY`) and webhook secret come from the environment via
  the secret provider, never config or logs. The Stripe SDK is an **optional
  extra** (`maverick-core[stripe]`), lazily imported; the module imports and the
  gate reports "disabled" cleanly without it. (Separate from the agent-facing
  `stripe` tool, which reads a customer's own account.)
- **Operator lifecycle commands** — one-command tenant onboarding, per-tenant
  feature control, and a go-live readiness gate over the existing primitives.
  `maverick tenant onboard <id> --admin-email <email>` provisions a tenant
  (plan, display name, spend cap) *and* mints its first-admin dashboard invite,
  printing the single-use link once (reuses `tenant.registry.create_tenant` +
  `maverick_dashboard.invites`). `maverick license issue|set|show|keygen` is a
  Click surface over `maverick.entitlements`: sign a deployment license
  declaring its tier + add-on suites, resolve the installed one, or mint a
  publisher keypair — the signing key is a secret sourced only from
  `MAVERICK_LICENSE_PRIVATE_KEY` (never config, never the repo, never printed).
  `maverick launch-check` prints PASS/FAIL for each go-live switch (cost-cap
  enforcement, per-user tenancy, a license trust anchor, at-rest encryption, a
  resolvable provider credential) and exits non-zero if any is off, so a deploy
  pipeline can gate on it. Also completes the config-lint/migrate registry for
  the wizard-written, kernel-read `[license]` section (no more false "unknown
  section" warning when pinning the license trust anchor).
- **Cortex, the company brain** — one governed recall/deposit surface
  (`maverick.cortex`) over the five memories the workforce already has:
  knowledge with citations, skills, self-harness lessons, the Operating Record
  and fleet memory. One query, one provenance envelope (source, kind, text,
  citation, score), one signed audit row per read (`cortex_recall`) and per
  write (`cortex_deposit`) recording who asked, which sources answered with how
  many hits, which were denied and why, and a digest of the query — never the
  text; the Agent Trust Plane gates external callers before any source is
  touched. Tenant-exact by construction, fail-open per source but never
  silently. New `maverick cortex status` (what each source holds plus the
  encryption-at-rest and isolation posture; `--json`), `maverick cortex recall
  QUERY` (hits grouped by source with citations; `--source`, `--domain`,
  `--model`, `--k`, `--json`, `--context` for the prompt block) and `maverick
  cortex deposit` (`--kind knowledge|fleet`, `--collection`, `--source`,
  `--domain`, `TEXT` or `--file PATH`). Default-on under `[cortex] enable`
  (`MAVERICK_CORTEX` overrides), offered as an advanced installer step beside
  fleet memory and included in the express and governance-profile presets.
- **Commerce suite** — a governed shopper + merchant agent suite (`com_`
  packs; `[commerce] enable` / `MAVERICK_COMMERCE`, default off, with a wizard
  step). Shopper side: search & compare, cart builder, product Q&A, order
  tracking & returns, and an explicit preference memory; merchant side:
  analytics, inventory, pricing, promotions, listing quality, and order ops.
  The kernel module `maverick.commerce` defines the `StorefrontBackend` /
  `MerchantBackend` interfaces plus an in-memory `DemoBackend`, and encodes the
  safety rules in code rather than prompts: per-session spend, per-line
  quantity, and cart-size caps; a provenance gate (every product fact carries
  its source and `fetched_at`); staged writes that only a human approval token
  can apply (price, inventory, listing, campaign, order resolution, return
  request); and a `checkout_handoff()` that returns an opaque cart reference
  and link — never payment, address, or checkout-page details. Design follows
  Anthropic's Apache-2.0 `commerce-agents` reference (no code copied).
- **Autonomous Shoring engagement tooling** (`maverick.shoring`, dashboard
  `/shoring` + `/api/v1/shoring`) — the surface for moving a whole business
  function onto the governed workforce: a tenant-scoped **Book of Work**
  (functions mapped to operating departments, each with a scope, an owner,
  named service-level targets, and a lifecycle in
  {scoping, demonstrating, running, proving}); an **SLA view** measuring
  target-vs-actual (throughput/day, exception rate %) from real completed work
  in the Operating Record, with a met/at-risk/breached indicator per SLA; and
  an **exception desk** — the pending human sign-offs filtered to each
  function's department, reusing the `/approvals` queue. The Shoring P&L is the
  existing Savings report. The book is a small governed JSON store (not a
  world-model migration); SLA/status computation is read-only. On by default
  (`[shoring] enable`, `MAVERICK_SHORING`), offered as an installer step beside
  Cortex and included in the express presets.
- **AI Evidence-Ready Gateway and operator cockpit** — governed evidence intake,
  review queues, readiness/health visibility, and first-run guidance now share
  one operator-facing workflow with durable audit checkpoints.
- **Governance Frontier benchmark publication** — the public report now ships
  beside its exact measured manifest, separately trusted Ed25519 publisher key,
  and a standalone verifier with tamper/ambiguity rejection.
- **Lockstep release-cohort delivery** — all eight Python distributions are
  validated and resolved together from one manifest across frozen binaries,
  containers, installers, CI integrations, and SBOM generation; release-runtime
  provider support and constrained dependency versions are checked before
  artifacts are published.
- **DPO preference-pair mining from real runs** — the offline DPO fine-tune
  (`maverick.training.rlaif`) now has two objective sources of a quality
  gradient, both built from donated run records (no hand-labels): a
  verifier-**rejected draft** paired against the accepted final within one
  `task_family`, and **best-of-N candidate mining** — `run_goal_best_of_n`
  scores each of N coding attempts by whether the task's real tests pass
  (`--fail-to-pass`/`--pass-to-pass`) and donates the pass-vs-fail spread as a
  preference pair with a ~1.0 reward margin, no verifier rejection needed. New
  `maverick start --repeat N` re-runs the same task N times so its attempts
  share a `task_family`. Trajectory donation is now reachable from the installer
  wizard (Advanced reasoning step, default off, metadata-only), and its
  thresholds are configurable under `[telemetry]` (`donate_min_entropy`,
  `donate_min_confidence`, `donate_text`) so a normal single-agent run is
  captured (the old swarm-only bar donated nothing from typical usage).
  End-to-end recipe in `docs/self-learning-runbook.md`.
- **FinOps & safety CLI + billing view** — `maverick spend` (`--json`) exports
  total + per-goal + per-tag run cost for BI/chargeback scripts; `maverick
  safety` (`--json`) prints the shield / sandbox-backend / egress posture to
  assert deployment safety in CI; and a dashboard `/billing` view shows the
  active tenant's accrued charges, a period-over-period trend, and an itemized
  CSV invoice (`/billing?format=csv&period=YYYY-MM`).
- **Obsidian-style goal graph** — `/graph-editor` now renders a force-directed
  constellation: circular nodes sized by connection count, hover spotlighting a
  node's neighbourhood, drag/pan/zoom, with the layered text tree as the
  accessible fallback. Edits (retitle / reparent / add child) and the cycle
  guard are unchanged.
- Roster & library growth: the specialist roster now stands at **2,020 packs
  across 53 suites**, with a **514-skill** reusable `SKILL.md` library any pack
  can activate by trigger.
- **Primary-source data connectors (37, read-only, low-risk)** — authoritative
  government/public data APIs (SEC EDGAR, FRED, Treasury, World Bank, FDIC,
  Census, BLS, EIA, Alpha Vantage, Finnhub, Polygon, OpenFIGI, Federal Register,
  eCFR, Regulations.gov, CourtListener, GovInfo, USAspending, SAM.gov, Open
  States, PatentsView, GLEIF, OpenCorporates, Companies House, openFDA, NPPES,
  ClinicalTrials, RxNorm, PubMed, NWS, NOAA Climate, OpenWeather, EPA Envirofacts,
  Climatiq, Carbon Interface). Built on three new `make_rest_tool` auth modes:
  `keyless`, `query_auth` (key on the query string), and `default_base_url`
  (zero-config public hosts).
- **Primary-source data grounding** — those connectors are auto-granted to each
  analyst pack by suite (`SUITE_DATA_CONNECTORS`, layered in `domain_capability`),
  so a pack reaches for the right source by default. Additive, low-risk, and
  deferred (no context cost); a host-restricted pack's egress is never silently
  widened. On by default with a kill-switch: `[workforce] data_grounding = false`
  / `MAVERICK_WORKFORCE_DATA_GROUNDING=off`, plus an installer wizard step.
- **Roster-wide governance invariant test suite** — six load-bearing invariants,
  each verified across all 2,020 packs with a non-vacuous fault-injection control
  (property-fuzzed up to 5,000 iterations): (1) tool reachability — no drafting agent can reach
  a state-mutating tool; (2) autonomy dial — an onboarding agent is never
  autonomous and a high-risk action is never autonomous even when graduated;
  (3) capability attenuation — a spawned child can never exceed its parent grant
  (no privilege escalation through the spawn chain); (4) compartment isolation —
  a quarantine seal never bleeds across compartments or suites; (5) hard refusals
  — the universal refusal floor is unstrippable; (6) budget caps — no cap is ever
  silently exceeded. Plus hostile-argument fuzzing of every connector and tool.
- Agent-pack quality & governance sweep across the 1,118-pack roster:
  `[output]` contracts + editable `[[workflow]]` playbooks on every pack;
  reasoning-effort right-sizing (`effort` tier, applied only when `[effort]` is
  on); always-on hard refusals (`domain_refusals.py` — EU AI Act Art-5 for HR,
  safety-critical actuation for the physical suites, autonomous adjudication for
  finance/clinical, MNPI-crossing for strategy, with a `refuse` pack field);
  `maverick domains-audit` (governance-posture inventory, `--json` export) and
  `maverick domains-eval` (behavioral golden cases + deterministic rubric
  scorer); query-based specialist routing (`list_specialists query=<task>`,
  hybrid lexical + sentence-transformer when `fastembed` is installed) with a
  recall@10 benchmark; factory-generated packs brought to parity (intake now
  emits workflow/output/effort/refuse). New `domains-lint` rules: read-only
  deny floor, output/playbook gate consistency, and `effort` validity.
- Closed-loop self-improvement: offline experience consolidation
  (`maverick dream` — replay, consolidate, rehearse, forget, prune),
  department-scoped reflexion/memory, skill distillation with probation and
  retirement, cross-department insight promotion, human-override and
  user-correction ingestion, and verifier-scored rehearsal gated by the
  calibration interlock.
- Learning governance: per-cycle `learning_update` audit rows, `--dry-run`,
  pre-cycle snapshots with `--rollback`, tenant-isolated learned stores, and
  the hindsight engine (`maverick hindsight`) for learning-regression
  detection.
- `maverick proof` — workforce value report (deliverables, cost avoided,
  ROI, improvement curve), with per-vendor `--fleet` breakdown.
- Cognitive Data Engine (`maverick flywheel`, opt-in `[data_engine]`) —
  causal failure triage (stratified ATE + confidence intervals + placebo
  refutation + a trustworthiness gate) → self-retiring guardrails →
  consolidated habits, composed in one pass; observable at
  `GET /api/v1/flywheel`.
- Operations Scientist (opt-in `[operations_scientist]`) — propose a
  harmful→beneficial action swap and validate it in a g-computation
  world-model before spending a real experiment.
- Consequence Engine (`maverick record-outcome`, `POST /api/v1/outcomes`,
  opt-in `[consequence]`) — a real downstream outcome overrides the
  verifier's self-graded proxy so learning is grounded in reality.
- Emergent Substrate (`maverick codebook` / `codec-learn` / `codec-probe`,
  opt-in `[emergent_protocol]` / `[emergent_codec]`) — an auditable
  coordination codec that learns short codes for repeated boilerplate;
  every code decodes EXACTLY back to English (`decode(encode(x)) == x`,
  fuzz-tested). `codec-probe` measures real token (not just byte) savings;
  the token-aware codec measures ~28% in benchmark. Measure-only on the live
  blackboard (`GET /api/v1/codec`); agents reading codes is a separate step.
- Fleet memory (`maverick fleet-memory`, MCP tools `maverick_fleet_ingest`
  / `maverick_fleet_recall`) — governed, audited memory plane for external
  agents.
- The Operating Record (`maverick record`) — decisions + approvals as a
  queryable system of record with Ed25519-signed, offline-verifiable
  capsule export.
- Federated insight exchange (`maverick insights-export` / `-import`,
  fail-closed signing) and fleet donation aggregation
  (`maverick dream --donations-dir`).
- Specialist portfolio expanded from 338 to 1,000 lint-clean agents across
  25 suites, including customer support, 10 industry verticals, and
  jurisdiction packs; suite operating discipline and department memory
  applied to every pack at spawn; `maverick domains-lint` quality gate.
- Goal rows persist their department (world-model schema v14).
- Tax preparation pipeline (`maverick tax prepare`, `tax_prep.py`): uploaded
  documents → deterministic classification/extraction → workpaper →
  first-pass TY2025 draft 1040 **and resident-state return** (auto-detected
  from W-2 box 15; no-tax/flat states computed, graduated states handed to
  the preparer) → preparer review package, every line cited to its source
  document and out-of-scope items flagged as open items; plus the 19-pack
  `tax_` specialist suite (portfolio now 1,118 packs across 26 suites) and
  CCH Axcess / Thomson Reuters GoSystem connectors (confirm-gated write
  seats + GET-only low-risk read seats for the status packs).
- Signed tax-constants channel (`maverick tax update`, `[tax]` config):
  new tax law arrives as an Ed25519-signed content bundle — fail-closed
  verification, sanity validation, downgrade protection, rollback, audit —
  auto-applied by `maverick tax prepare` when `[tax] auto_update` +
  `update_url` are configured; the `tax_law_watch` pack monitors IRS/state
  guidance (web access, no client-data access) and alerts the firm.
- **Coding mode no longer runs a workspace's git commands on the host** — the
  patch apply, workspace reset and patch check run `git` on the host, and git
  runs whatever filter, hook or `fsmonitor` command the workspace's own git
  settings name. Those settings are now read wherever git reads them (a `.git`
  pointer file or symlink, a linked worktree's main repository,
  `config.worktree`), repository hooks are switched off, and a workspace whose
  git settings define a filter is refused rather than run. A repository that
  really uses a filter, such as Git LFS, therefore gets no host-side patch
  apply or check in coding mode; running those git steps inside the sandbox is
  proposed in `docs/proposals/coding-mode-sandbox-routing.md`.
- **A task started through a specialist pack keeps the requester's limits** —
  the pack's own tool list used to replace the requester's role, risk ceiling
  and any compliance restriction (for example the HIPAA / SOC 2 ban on outbound
  tools), so a read-only person could write files by starting the task through
  a pack. The pack's tools now only ever narrow what the requester may do.
- **"Ask a person before starting risky-looking tasks" now applies to every way
  of starting a task** — it was skipped for tasks started from the dashboard, the
  API, a schedule, a flow or the job queue. A task that already has a run on
  record (a retry, or one continued after an answered question) is not asked
  about again.
- **The REST API says when a blocked task is only waiting for an answer** —
  `GET /api/v1/goals/{id}` and the events response carry `needs_input` and
  `open_question_ids`, the events response reports `terminal: false` while the
  task waits, and a goal carries `created_at` / `updated_at`. A machine-readable
  reason for the other kinds of blocked task is not part of this change.


### Changed
- **CI runs the suite in parallel and `main` is protected** — the test matrix
  and the posture job run as three disjoint file-level slices per interpreter,
  each under pytest-xdist (one worker per core, tests grouped by file), taking
  a leg from ~27 minutes serial to single digits on GitHub's two-core runners;
  the root conftest fails any test that leaves a `maverick.*` module forked
  between the package attribute and `sys.modules` (the order-dependent leak
  parallel runs exposed); the repo-wide contract gates memoise their source
  scan;
  `main` is governed by the importable ruleset in
  `.github/rulesets/protect-main.json` (pull requests only, squash only,
  linear history, every blocking check required, merge queue) with every
  required workflow triggering on `merge_group`; and a `no-ai-attribution`
  check refuses AI co-author trailers, generator footers and assistant
  session links in commit messages and PR text before they can reach `main`.

### Fixed
- **Dashboard `/agents` and `/workflows` latency** — the built-in pack catalog
  (2,000+ packs) was re-parsed from disk on every request, making those pages
  ~75× slower than the rest of the dashboard. The built-in catalog is now
  memoised per process (keyed by its directory mtime; tenant overrides still
  load live), cutting both pages from ~700ms to ~10–20ms.
- Goal-graph links at `/graph-editor` now render as a clearly visible
  soft-grey (the previous near-invisible tone read as unconnected nodes).
- Robustness defects surfaced by the 1M-iteration stress sweep (each with a
  regression test): connectors no longer raise on a non-string `op`/`path`/
  `query` — a malformed value now returns an `ERROR:` string instead of an
  `AttributeError` that crashed the call for all ~250 REST + GraphQL connectors;
  `Skill.parse` raises `ValueError` (its documented failure mode), not
  `AttributeError`, on malformed/untrusted frontmatter (a YAML list item under a
  scalar key), so an `except ValueError` guard around skill loading holds;
  `format_money` degrades gracefully on a `None`/empty currency instead of
  crashing on `None.upper()`.
- Dual control: the "requester cannot approve their own request" segregation-of
  -duties rule now fires for real dashboard approvals. The consent gate records
  the executing goal's owner as the approval's requester, so an N-of-M approval
  can no longer be self-approved by its initiator (previously the requester was
  never recorded, leaving the bar unreachable outside tests).
- Best-of-N now sets each attempt's sampling temperature via a per-goal
  ContextVar instead of a process-global `MAVERICK_TEMPERATURE` env var, so two
  goals running concurrently in one process no longer read each other's
  temperature. The env var is still honoured as a process-wide fallback.
- Per-tenant RBAC role assignments are rejected (409) under per-user tenancy
  (`MAVERICK_TENANT_BY_USER`), where every request is pinned to the caller's own
  isolated tenant and a named-tenant role could never take effect — turning a
  silent no-op into an explicit error. Named-tenant deployments are unaffected.
- MCP server returns `-32602` (invalid params), not a scrubbed `-32603`, for a
  non-string `uri` (`resources/read`) or a non-string/non-hashable tool `name`
  (`tools/call`).
- World model search now backfills the `messages_fts` full-text index on
  upgrade (schema v10). The FTS triggers only index future writes, so a
  database whose messages predated the index carried unindexed history that
  message search silently missed; v10 rebuilds it once.
- Self-learning data pipeline (donation → ingest → DPO) closed several gaps that
  left the training corpus empty: trajectory records now carry a `goal_id` (so
  `ingest`/`export_texts` can join the world DB for transcripts) and a
  `task_family` derived from the task-brief hash (without it `rlaif` grouped
  everything into one family and mined zero pairs); the swarm-disagreement
  donation gate is now configurable so single-agent runs are no longer silently
  dropped.
- Best-of-N candidate capture now recovers patches an agent applied via tools
  (no diff in its final answer) by diffing the workdir, excludes `__pycache__`/
  `*.pyc` and binary sections (which made `git apply` reject the whole patch and
  scored every candidate 0), and resets the shared workdir to clean HEAD between
  attempts so one attempt's edits can't leak into the next. New
  `MAVERICK_BON_EARLY_EXIT=0` runs all N attempts (instead of stopping at the
  first pass) so a failing candidate is also captured for the preference pair.

## [0.1.6] -- 2026-05-31

Post-0.1.4 hardening, plus the cross-language MCP client surface (the
council's "drive Lightwork from any language over MCP" decision) and the
polyglot sandbox / coding-mode work.

### Added
- **Cross-language MCP clients.** Runnable example clients and quickstarts for
  **TypeScript / JavaScript, Go, Rust, C# / .NET, and Java / JVM** -- each one
  exercised in CI against a live `maverick mcp` (initialize -> tools/list -> a
  no-LLM tool call). Lightwork stays a single Python kernel; any MCP-speaking
  language drives it over stdio JSON-RPC. See `docs/clients/*-quickstart.md`.
- **Per-language sandbox toolchains.** Container backends now pick their image
  from `[sandbox] language` (rust -> rust:1, go -> golang:1, JS/TS -> node:22,
  Java/Kotlin -> eclipse-temurin:21, ruby -> ruby:3; Python is the default),
  and the installer wizard asks which language you code in -- so `cargo test` /
  `go test` / the JS runner actually run instead of failing on python:3.12-slim.
  coding-mode gained Rust/Go/TypeScript failure-pattern hints, and
  `maverick doctor` reports a missing language toolchain.
- **Agent-to-Agent (A2A) task endpoint** with push-notification config, an
  installer wizard step, and docs.

### Security
- **SMS and WhatsApp now enforce a per-sender allowlist** (default-deny),
  closing a critical auth bypass: in 0.1.4 these two Twilio channels checked
  only the `X-Twilio-Signature` (which proves Twilio relayed the message, not
  that the *sender* is authorized), so any PSTN subscriber who texted the
  number could drive the swarm with host shell access. They now match the
  other channels: a valid signature plus an explicit allowlist member.
- **GDPR erase now re-anchors the signed audit chain.** When `[audit] sign`
  is enabled, scrubbing/tombstoning a user's rows used to break the Ed25519
  hash-chain at the erasure point -- a break `maverick audit verify` could
  not tell apart from tampering, so a routine privacy operation destroyed
  the trust anchor. erase re-chains and re-signs the affected files (the
  signed `erase` marker records the authorized cut) so verification passes
  again. Also de-duplicated a double `scrub_user` call in the erase path.
- Bumped dependency floors past known CVEs on the network-facing surfaces:
  `pillow>=12.2.0` (5 CVEs incl. PYSEC-2026-165), `python-multipart>=0.0.27`
  (CVE-2026-42561), `requests>=2.33.0` (CVE-2026-25645), `starlette>=1.0.1`
  (PYSEC-2026-161), `urllib3>=2.7.0` (PYSEC-2026-142/141). starlette is
  declared directly where the dashboard imports it; requests/urllib3 are
  floored in the Twilio (`sms`/`whatsapp`) extras that pull them in.
- The CI `pip-audit` job is now blocking, so a new advisory fails the build
  and prompts a floor bump instead of shipping silently.

### Changed (breaking)
- The `sms` and `whatsapp` channels **require** an allowlist to start. Set
  `SMS_ALLOWED_USER_IDS` / `WHATSAPP_ALLOWED_USER_IDS` (comma-separated) or
  the `allowed_user_ids` config key, or the channel raises on startup. List
  senders as Twilio delivers them: `+14155551234` for SMS,
  `whatsapp:+14155551234` for WhatsApp. (Slack/Signal/Matrix already required
  this as of 0.1.4 -- SMS/WhatsApp were missed.)

## [0.1.4] -- 2026-05-30

The launch-hardening pass that landed just after the 0.1.3 tag was cut.
0.1.3 shipped without these; 0.1.4 is the first release to include them.

### Added
- Per-sender channel allowlists for Slack/Signal/Matrix/Voice (default-deny);
  the installer wizard now collects `*_ALLOWED_USER_IDS` / allowed callers.
- `compute`/sympy math tool wired up (was dead); restored two
  silently-shadowed CLI commands (GDPR `export`, `logs`).

### Fixed
- **Killswitch is now enforced.** `maverick halt`, the dashboard Halt button,
  and the HALT file are checked at the agent turn boundary and the tool
  boundary -- in 0.1.3 they were read by nothing (a no-op).
- **MCP HTTP transport** no longer crashes with `asyncio.run() ... running
  event loop`; client-supplied budgets are clamped and an arbitrary host-file
  read (`trusted_local`) was closed.
- **VPS installer** references the correct pipx package name (`maverick-agent`)
  and its run-as-user model is coherent.
- GDPR `erase` no longer fails on the foreign-key cascade; audit `scrub_user`
  is wired.
- Cost accounting for router-selected models; best-of-N now rolls all budget
  counters (cache tokens + tool calls) into the parent and enforces its cap.
- CircuitBreaker HALF_OPEN admits exactly one probe (was a retry storm); MCP
  negotiates protocol version by supported set, not a lexicographic downgrade.
- `dep_graph` emits forward-slash paths on every platform; arXiv API uses
  https; Windows POSIX file-mode test assertions are guarded.

## [0.1.3] -- 2026-05-29

### Added
- One-line desktop installers: `deploy/desktop/install.ps1` (Windows,
  `irm ... | iex`) and `deploy/desktop/install.sh` (macOS/Linux,
  `curl ... | bash`). They install Python 3 + git if missing, set up an
  isolated pipx environment, and launch the wizard -- no prerequisites.

### Fixed
- Windows installer could not find a just-installed Python: winget runs
  the python.org installer without adding it to PATH. Detection now
  falls back to the PEP 514 registry and well-known install dirs, and
  probes with `--version` instead of a quoted `python -c "..."` snippet
  (Windows PowerShell 5.1 mangles embedded double quotes, which made
  every probe fail even when the interpreter was fine).
- Release binaries crashed with `No module named 'maverick'` -- the
  build installed packages editable, which PyInstaller can't collect.
  Now installed non-editable, with `collect_submodules`/`copy_metadata`
  in the spec.

### Changed
- The MCP server is published to PyPI as `maverick-mcp-server` (the
  `maverick-mcp` name is taken by an unrelated project). The import
  package (`maverick_mcp`) and the `maverick-mcp` command are unchanged.
- PyPI publishing runs one job per package (`fail-fast: false`), so a
  package without a trusted publisher can't abort the others.
- Repository references updated to the `cdayAI` GitHub account.

## [0.1.1] -- 2026-05-25

### Fixed
- PyInstaller release binaries on all three platforms (Linux x86_64,
  macOS arm64, Windows x86_64) failed at `import sqlite3` -- the
  v0.1.0 build flags missed bundling stdlib `sqlite3`. Switched to a
  `.spec` file with an explicit `hiddenimports` list and pinned
  PyInstaller to `>=6.0,<7.0`. A diagnostic step now verifies
  `sqlite3` is importable on the build host before the bundle runs.
- `maverick version` reported `maverick: not installed` after the
  PyPI rename to `maverick-agent`. The reporter now reads
  `maverick-agent` as the canonical distribution name.

### Added
- Multi-turn conversation state per channel user (schema v3 -> v4).
- File + image input on goals (schema v4 -> v5) with mime allowlist,
  per-file/per-goal quotas, vision-block delivery for images.
- Plugin SDK via `importlib.metadata` entry_points for tools, channels,
  skills, personas. Fault-isolated.
- `benchmarks/harness.py` + RESULTS.md.
- `ask_user` is now scoped to the running goal.
- Council medium-priority polish: docs, schema migration tests,
  orchestrator E2E test, channel adapter smoke tests, /chat/goal
  user-friendly labels, multi-line REPL.

## [0.1.0-alpha]

First public release. Lightwork combines [Lightwork Agent](https://github.com/cdayAI/research/tree/main/maverick) (recursive multi-agent swarm)
and [Agent Shield](https://github.com/cdayAI/agent-shield) (safety detection)
into a single safest-by-default agent that anyone can install in one
command.

### Added

**Agent kernel** (`maverick-core`)
- Recursive multi-agent swarm: `spawn_subagent` (blocking) and `spawn_swarm` (parallel via `asyncio.gather`)
- Persistent SQLite + FTS5 world model: goals, episodes, facts, questions, messages
- Hard budget caps: tokens, $, wall-clock, tool calls
- Auto-distilled skills: successful trajectories → reusable SKILL.md
- WorldModel schema migrations with version tracking (currently at v2)

**Multi-provider LLM dispatch**
- Anthropic (full impl: prompt caching, extended thinking, streaming)
- OpenAI (Chat Completions + tool-use, with Anthropic format translation)
- OpenRouter (200+ models via single API)
- Ollama (local models, OpenAI-compatible at `localhost:11434`)
- Gemini (Google, via OpenAI-compatible endpoint)
- Per-role model assignment via `[models]` section of config

**Safety** (`maverick-shield`)
- Agent Shield SDK integration when installed (full ~115 patterns)
- Built-in fallback rules (~20 high-impact patterns) when SDK absent
- Three chokepoints: input scan, tool-call scan, output scan
- Profiles: strict / balanced / permissive / off
- Shield is actually wired into the agent loop (not just documented)

**Channels** (`maverick-channels`)
- Telegram (Bot API), Discord (Gateway), Slack (Socket Mode)
- Signal (via signal-cli subprocess)
- Email (IMAP poll + SMTP send, stdlib only)
- Matrix (federated, via matrix-nio)
- WhatsApp + SMS (Twilio, with signature verification)
- iMessage (macOS, parameterized AppleScript, no shell injection)

**Sandboxes**
- Local subprocess
- Docker (throwaway containers, `--network=none` by default)
- SSH (remote host via system ssh binary)

**Installer**
- `maverick init`: interactive CLI wizard with preflight + API-key validation
- Tauri-based native GUI installer scaffold (`apps/installer-desktop/`)
- Per-role model picker, channel picker, safety profile picker
- Channels prompted for required env vars only

**Web dashboard** (`maverick-dashboard`)
- Local FastAPI app at `127.0.0.1:8765` showing goals / skills / facts / spend
- Dark monospace theme, no JS framework, htmx for the live bits

**MCP server** (`maverick-mcp-server`)
- Lightwork exposed as a Model Context Protocol server over stdio
- Drives the swarm from Claude Code / Cursor / Claude Desktop / any MCP client
- 8 tools: start / status / resume / answer / skill_install / skills_list / fact_set / facts_get

**CLI commands**
- `maverick init` / `start` / `serve` / `doctor` / `config` / `dashboard` / `mcp`
- `maverick logs` / `status` / `answer` / `resume` / `fact` / `facts`
- `maverick skill install/remove/info` / `skills`

**Distribution**
- Release workflow: GHCR Docker image push on tag
- PyInstaller single-file binaries for Linux x86_64 / macOS arm64 / Windows x86_64
- PyPI publish for all 6 packages (gated on `PYPI_API_TOKEN` secret)
- VPS bootstrap: `install.sh` + systemd unit + Caddyfile

**Tests**
- Smoke tests across all packages (imports, config, budget, blackboard, etc.)
- OpenAI format translator: 17 unit tests covering round-trip fidelity
- Skills: install / remove / parse / safe_name / relevance scoring
- Built-in shield rules: each rule category + profile interactions
- Agent loop tests using FakeLLM fixture: FINAL parsing, ask_user blocking,
  Shield blocking, budget exhaustion, max_steps cap
- MCP server: tool catalog + protocol shapes
- Dashboard: every page renders with empty data

**Documentation**
- `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `CHANGELOG.md`
- `docs/getting-started.md`, `docs/configuration.md`, `docs/deployment.md`, `docs/safety.md`
- 5 example skills (`benchmarks/example-skills/`)
- 3 long-horizon benchmark tasks with budget/criteria

### Known limitations (v0.1.0-alpha)

- No PyPI publication yet (CI workflow is ready; just needs `PYPI_API_TOKEN`)
- No notarized DMG / signed MSIX (Tauri scaffold exists; signing comes next)
- Skill retrieval is lexical (embeddings-based retrieval is v0.2)
- WhatsApp and SMS scaffolds require a public HTTPS endpoint to receive Twilio webhooks
- Agent Shield SDK not yet on PyPI (built-in fallback rules cover ~20 high-impact patterns until then)
