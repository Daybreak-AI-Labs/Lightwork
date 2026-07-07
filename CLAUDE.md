# CLAUDE.md — the Lightwork operating manual

This file is the operating manual for any model working in this repo. Every
rule is backed by a named failure, a CI gate, or a verified run — nothing here
is style preference. Last full local verification: 2026-07-07 — ruff +
vulture + all 11 custom gates green; suite: 15,386 passed / 196 skipped /
3 xfailed in ~10 min (don't assume a hang before that).

**Prime directive:** if reality contradicts this file, trust reality — then
update this file (and AGENTS.md) in the same PR, with the evidence. A stale
rule is worse than no rule.

## 1. What this is

Lightwork = proprietary AGENTIC ENTERPRISE PLATFORM: a governed,
self-improving AI workforce — kernel + shield + channels + dashboard + mcp +
evolve + knowledge, ~2,020 lint-clean specialist packs, a closed learning
lifecycle (dreaming/hindsight/proof, snapshot + rollback, signed learning
audit), fleet memory, and the Operating Record. Category note: free OS agent
runtimes (OpenClaw etc.) are NOT the competitive category — they are a
commodity layer. Compete with enterprise platforms (Agentforce, Copilot,
Gemini Enterprise, ServiceNow) on governance + provable learning, never on
the runtime.

Python 3.10–3.12 uv-workspace monorepo. Import namespace is `maverick`
everywhere; the product name is Lightwork (`lightwork` CLI alias exists;
`LIGHTWORK_*` env vars mirror to `MAVERICK_*`, MAVERICK_ wins).

| Package | What / entry point | Watch out |
|---|---|---|
| `packages/maverick-core` | The kernel. CLI `maverick` (`maverick/cli/__init__.py`, ~6.9k lines). PyPI dist name is `maverick-agent` | 361 modules, `agent.py` alone is 173 KB — navigate by grep, never enumerate |
| `packages/maverick-shield` | Safety chokepoints (`guard.py`). Zero hard deps; `agent-shield` SDK is an optional extra | Kernel must run without it (kernel rule 1) |
| `packages/maverick-channels` | Platform adapters (telegram/discord/slack/…), each an extra | Inbound allowlist is fail-closed: no `allowed_user_ids` = channel refuses everyone |
| `packages/maverick-dashboard` | FastAPI+htmx UI, `maverick_dashboard.app:app` (`app.py`, ~5.5k lines) | CSRF + auth-exempt lists, §5 traps 14–16 |
| `packages/maverick-evolve` | Governed config-only self-evolution | Code self-modification is out of scope by design |
| `packages/maverick-knowledge` | Per-domain RAG, zero hard deps | Ingest rejects whole docs on injection tripwires — intentional |
| `packages/maverick-mcp` | MCP server (dist `maverick-mcp-server`), protocol `2025-11-25` + 3 fallbacks | Don't bump protocol without the spec |
| `apps/installer-cli` | The wizard (dist `maverick-installer`) | The wizard is the UX source of truth (kernel rule 6) |
| `sdks/plugin-ts` | `@maverick/plugin-sdk`, Node 22, NDJSON-over-stdio | `npm test` = tsc + node --test; no jest |

Tests live flat at `packages/<pkg>/tests/test_<module>.py`; the module is the
unit (`money_format.py` → `test_money_format.py`). Tool wrappers under
`tools/` often have NO standalone test file — grep before assuming a name.

## 2. Environment: fresh container → working state

```bash
# Debian-based images: pip cannot upgrade the distro cryptography 41.x
# (no RECORD file) and maverick-core now floors cryptography>=44.0.1,
# so post-create dies on step 1 without this (hit + verified 2026-07-07):
pip install --ignore-installed 'cryptography>=44.0.1'
bash .devcontainer/post-create.sh
```

Verified commands:
- **Test:** `python3 -m pytest -q` from repo root. NEVER bare `pytest` (§5, trap 1).
- **Lint:** `python -m ruff check .` and `python -m vulture` (no args).
- **All lint-job gates locally:** `bash scripts/pre-push-lint.sh` — but it
  covers only 10 of CI's 16 lint steps; `/preflight` (repo skill) runs all of
  them plus the audit-job parity checks. Use the skill before any push.
- **Build:** `python3 -m build --wheel` per package dir.
- **Smoke:** `maverick version` (prints package versions + `schema: v27`),
  `maverick doctor` (exits 1 on any failed check).
- **TS SDK:** `cd sdks/plugin-ts && npm install && npm test`.

Fresh-environment facts: `ANTHROPIC_API_KEY` and `~/.maverick/config.toml`
are absent, so live-LLM paths are untestable, `maverick doctor` reports
failures, and `/healthz` returns 503 — all by design, not bugs. mkdocs is not
installed by post-create.sh. `pip install -e .` at the repo root fails (the
root pyproject is uv-workspace + tool config, no `[project]`); install
per-package, core first WITH deps, the rest `--no-deps`.

## 3. How the maintainer works — follow this shape

Observed across ~330 commits; deviating from it reads as noise in review.

- **Campaign branches.** One topic = one `claude/<slug>-<suffix>` branch = one
  PR; a campaign lands a sequence of related commits, then merges.
- **Surgical commits, test included.** A typical functional commit touches
  2–3 files and carries its own regression test IN THE SAME DIFF. Big sweeps
  are legitimate only for mechanical copy/rebrand work.
- **Commit messages:** Conventional Commits, imperative, lowercase subject
  starting with a letter. The body explains WHY (not what), and functional
  commits end with a `Tests:` line naming what the new test pins.
- **The review ritual:** after landing a feature, run `/code-review`,
  `/simplify`, and (for anything touching auth/tools/subprocess/crypto)
  `/security-review`, then land the findings as a follow-up `fix:`/`refactor:`
  commit before merge. This is a signature habit (e.g. "8-angle high-effort
  pass"), not optional polish.
- **Never merge on red or still-running CI.** Required checks: `lint`,
  `lint-pr-title`, `test (3.10/3.11/3.12)`, `audit`, `docker`.
- **Numbers are sacred.** Never fabricate or extrapolate benchmark, audit, or
  coverage figures. The house style is quantified honesty ("8 confirmed / 4
  downgraded / 1 refuted"). No real key → say "unmeasured", never estimate.

## 4. The gates — what CI actually runs (audited 2026-07-07)

`ci.yml` jobs: `lint`, `audit`, `test` (3.10/3.11/3.12 matrix, `pytest -vvs`),
`redteam` (`python -m maverick_shield.redteam` vs corpus), `eval-smoke`
(`benchmarks/test_eval_smoke.py`), `postgres` (pgvector live tests), `docker`
(image build + `--help` smokes). Plus `lint-pr-title` (see §8).

The 16 lint-job steps, in CI order — all runnable locally:

```bash
python -m ruff check .                            # E/F/W/I/UP/B/G/RSE/C90≤20/…
python -m vulture                                 # dead code ≥80% confidence
grep -rn --include='*.py' -E '^import[[:space:]]+tomllib([[:space:]]|$)' apps packages benchmarks  # must be EMPTY
grep -rn --include='*.py' 'shell=True' apps packages | grep -v '/sandbox/' | grep -v '/tests/'     # must be EMPTY
# detect-secrets: new (file, hash) pairs vs .secrets.baseline fail; scans TRACKED files only
python -m maverick.plugin_matrix --ci
maverick domains-lint --ci                        # NEW: all 2,020 domain packs validated
python -m maverick.deprecations --ci              # past-due deprecations fail
python -m maverick.grpc_api.contract --check      # additive-only proto; --regen is a reviewed act
python -m maverick.a11y_audit --ci                # static WCAG over dashboard templates
python -m maverick.failure_policy --ci            # NEW: broad excepts in maverick/audit/* need "# failure-policy: ..."
python -m maverick.schema_migrations --ci         # migration SQL-shape lint
python -m maverick.migration_governance --ci      # released migrations immutable; locks in migrations.lock.json
python -m maverick.evaluator_evolution --ci       # released anchors immutable; evaluator_anchors.lock.json
python -m maverick.control_data_plane_e2e --ci    # goals run out-of-process, exactly-once
python -m maverick.control_data_plane_soak --ci   # same under concurrent workers
```

The `audit` job is BLOCKING (it wasn't always): `pip-audit` (any known CVE
fails), `bandit -q -r packages apps -lll -ii` (high-sev/high-conf; by-design
findings get `# nosec <id>`), and a license-policy check (strong copyleft
deps fail). `docs.yml` runs `mkdocs build --strict` but only on push to main
— broken doc links bite AFTER merge, so check links when editing docs.

Ruff reality (updates older docs): **bugbear (B) is ON repo-wide**, along
with G (logging %-style, never f-strings in log calls), RSE, and curated
PIE/C420/RUF010/SIM300/FURB rules. ~94 inline `# noqa: B*` are grandfathered
debt — don't strip or "fix" them in unrelated PRs, and don't add new ones.
Complexity is capped at 20 (`# noqa: C901` marks grandfathered functions —
new code stays under). There is NO mypy/pyright anywhere; ruff is the only
static gate, so don't ship type-only "fixes" expecting a checker to validate
them.

## 5. Named traps — the mistakes a weaker model makes here

Each trap is named, real, and has exactly one preventing rule.

**Environment & test-running**

1. **The bare-pytest trap.** PATH `pytest` can be a uv-tool/pipx shim whose
   isolated venv can't see the installed packages (observed: 173 collection
   errors from the shim, 0 real). *Rule: always `python3 -m pytest` from the
   repo root.*
2. **The green-tail illusion.** `cmd | tail` reports tail's exit code, not
   cmd's — it masked a full collection abort here. *Rule: never pipe a gate
   command; use a log file or `${PIPESTATUS[0]}`.*
3. **The collection-abort misread.** One broken import aborts the ENTIRE run
   — 5 bad imports once kept 9,000 green tests from running, and a
   `pyo3_runtime.PanicException` hides the real error (`No module named
   '_cffi_backend'`) on the captured-stderr line above it. *Rule: on any
   collection error, fix imports first and read the stderr line above a pyo3
   panic; judge nothing about the suite until collection is clean.*
4. **The distro-cryptography wall.** Fresh Debian containers can't
   pip-upgrade system cryptography (no RECORD file) and core floors
   `>=44.0.1`. *Rule: `pip install --ignore-installed 'cryptography>=44.0.1'`
   before post-create.sh.*
5. **The wandering cwd.** Shell cwd persists between steps and pytest
   behavior depends on it. *Rule: anchor every pytest invocation at the repo
   root with an absolute path.*

**Kernel invariants (violating these is a rejected PR, even if green)**

6. **The helpful fail-closed "fix".** Shield scans, governance lookups, and
   policy checks fail OPEN with a warning when the shield is absent — a
   weaker model "fixes" this into fail-closed and breaks kernel rule 1.
   Production flips posture via `MAVERICK_SECURE_DEFAULT=1` /
   `MAVERICK_REQUIRE_SHIELD`; tests pin it to 0 deliberately. *Rule: never
   convert a fail-open seam to fail-closed (or vice versa) — posture changes
   go through config, and the seam is `maverick/shield_policy.py`.*
7. **The hard-coded model id.** *Rule: `maverick.llm.model_for_role(role)`
   (public entry; enforces the admin allow-list) or
   `maverick.config.get_role_model(role)`; literals in `maverick.llm`
   (`ROLE_MODELS`, `DEFAULT_MODEL`) are the only sanctioned defaults.*
8. **The single-cap test trap.** `Budget` enforces ALL caps inside
   `record_tokens`/`record_tool_call` (tokens, $, wall, tools) — not just at
   `check()`. A test raising only `max_dollars` still dies on the default
   `max_output_tokens=200_000`. *Rule: raise every cap you'll cross; never
   bypass `budget.check()` (kernel rule 3). Cache tokens are separate
   counters and don't count toward `max_input_tokens`.*
9. **The subprocess shortcut.** `shell=True` outside `maverick/sandbox/` is a
   grep-gate failure, and the string literal in a comment ALSO trips it.
   *Rule: all shell through `sandbox.exec()`; get a sandbox only via
   `build_sandbox()`, never by instantiating a backend class.*
10. **The bare tomllib import.** `tomllib` is 3.11+ stdlib; this broke three
    PRs. *Rule: `try: import tomllib` / `except ModuleNotFoundError: import
    tomli as tomllib` — the gate greps column-0 imports only, so the
    indented fallback passes.*

**Ratchets (one-way doors — see §7 before touching)**

11. **The migration edit.** Released world-model migrations are immutable;
    editing one, removing one, or adding a DROP/RENAME fails
    `migration_governance --ci`; SQLite and Postgres ladders must share the
    same head. *Rule: append-only on BOTH ladders, bump `SCHEMA_VERSION`,
    regen + commit `migrations.lock.json` — use `/world-schema-migration`
    (repo skill).*
12. **The anchor edit.** Evaluator ground-truth anchors are immutable once
    released (a mutable anchor launders evaluator drift). *Rule: new anchors
    get locked via `evaluator_evolution --regen`; never touch a released
    anchor's labels; `--force` requires explicit maintainer sign-off.*
13. **The proto "cleanup".** `maverick.v1` gRPC is additive-only; removals,
    renames, renumbers, and type changes fail the contract gate. *Rule:
    breaking change = new `maverick.v2` package, never an edit; additive
    changes need `contract --regen` + committed golden.*

**Dashboard & web**

14. **The health-endpoint reinvention.** *Rule: never add `/api/v1/health` —
    `/healthz`, `/livez`, `/readyz` exist, deliberately auth-exempt (LB
    probes can't carry a bearer; payload self-redacts when
    `MAVERICK_DASHBOARD_TOKEN` is set). Auth exemptions live in TWO lists
    that must stay in sync: `app.py` `_AUTH_EXEMPT` and `auth.py`
    `_OIDC_EXEMPT_PATHS`.*
15. **The 403ing dashboard test.** Mutating `/api/v1` requests in no-token
    mode must pass a same-origin CSRF check that fails closed without an
    Origin/Referer. *Rule: `TestClient(app, headers={"Origin":
    "http://testserver"})` plus an autouse fixture monkeypatching
    `world_model.DEFAULT_DB` to `tmp_path / "world.db"` and clearing
    `app_mod._world_cache` — copy `test_ux_endpoints.py`.*
16. **The unpackaged template.** Dashboard `templates/*.js` are
    Jinja-included; missing package-data = 500 `TemplateNotFound` only in
    BUILT apps (worked in dev, failed packaged — real PR). *Rule: new
    template/static assets must match the package-data globs in the
    dashboard pyproject.*

**Tests & hygiene**

17. **The silently-skipped async test.** pytest-asyncio runs STRICT; an
    unmarked async test doesn't run. *Rule: every async test carries
    `@pytest.mark.asyncio` explicitly.*
18. **The real-credentials reflex.** *Rule: the LLM is always faked —
    `FakeLLM` fixture with `.scripted` responses and `.calls` assertions;
    never burn API credits in tests; secret-looking fixtures need `# pragma:
    allowlist secret` or detect-secrets fails on the new hash.*
19. **The import-direction mixup.** *Rule: relative imports inside a package
    (`from ..money_format import …`), absolute in tests (`from
    maverick.budget import …`).*
20. **The unlogged broad except.** In `maverick/audit/*` every broad
    `except` must declare its classification. *Rule: annotate with
    `# failure-policy: <classification>` or `failure_policy --ci` fails.*
21. **The drive-by improvement.** Grandfathered `# noqa: B*`/`# noqa: C901`,
    the `@app.on_event` deprecation in `app.py`, starlette's TestClient
    warning — all known, intentional, pending. *Rule: surgical diffs; fix
    only what the task names; leave adjacent debt alone.*

**Docs & renames**

22. **The stale-docs rename.** A module rename that doesn't update
    `docs/FEATURES.md` and any `tools/` wrapper ships lies (FEATURES.md is
    "grounded in the code on main" — stale claims are a flagged credibility
    risk). *Rule: rename = code + FEATURES.md + tools/ wrapper + tests, one
    commit. `git checkout -- .` after a `git mv` leaves the new file
    untracked; follow with `git clean`.*
23. **The unreachable feature.** A capability with a config knob but no
    wizard step doesn't exist for users (real case: the Consequence Engine
    shipped kernel-side but couldn't be enabled). *Rule: new capability =
    config knob + `migrate.KNOWN_SECTIONS` entry + wizard step + parity
    tests — use `/add-capability` (repo skill).*

## 6. Quality bar per deliverable — checkable, not adjectives

**Any PR (baseline):**
- [ ] Title: Conventional Commits type + subject starts with a LETTER (§8).
- [ ] `python -m ruff check .` and `python -m vulture` exit 0.
- [ ] `python3 -m pytest -q` from repo root: 0 failed, 0 error, no new skips.
- [ ] All 16 lint-gate commands in §4 exit 0 (or `/preflight` passes).
- [ ] `git diff --stat` contains only files the task required.
- [ ] No new dependency without a config knob (kernel rule 5) — and say so
      in the PR body.
- [ ] PR body follows `.github/PULL_REQUEST_TEMPLATE.md` (What/Why/How/Test
      plan/Checklist).

**Bug fix:**
- [ ] Regression test in the SAME commit, named after the module under test.
- [ ] Proof the test bites: it fails when the fix is reverted
      (`git stash && python3 -m pytest <file> -q` shows the failure, then
      `git stash pop`). State this in the commit body's `Tests:` line.
- [ ] Root cause named in the commit body (why, not what).
- [ ] Diff touches ≤3 files unless the bug is genuinely cross-cutting.

**New feature / capability:**
- [ ] Everything in `/add-capability`: kernel code reads config (not env
      literals), knob in `KNOWN_SECTIONS`, wizard step + emits/omits parity
      tests, off by default, declined = no config section written.
- [ ] New tools go through `ctx.shield.scan_tool_call()`; long-running paths
      respect `Budget`; shell via `sandbox.exec()`.
- [ ] `docs/FEATURES.md` entry (present tense, module path); CHANGELOG.md
      `[Unreleased]` entry (feature name + knob + kill-switch); CLI verbs in
      `docs/getting-started.md`.
- [ ] Review ritual run (`/code-review`, `/simplify`, `/security-review` if
      it touches auth/tools/subprocess), findings landed as follow-up commit.

**Test-only change:**
- [ ] Placed at `packages/<pkg>/tests/test_<module>.py` (grep first — the
      module may already have a home).
- [ ] No network, no real keys, `FakeLLM` for LLM paths, every Budget cap
      you'll cross raised, async tests marked.
- [ ] Deterministic: no sleeps-as-sync, no time-of-day dependence (de-flake
      commits are a recurring tax here — don't add to it).

**Refactor / rename:**
- [ ] Behavior-neutral: suite green before AND after with no test edits
      (except import paths).
- [ ] FEATURES.md + tools/ wrappers + skills/docs references updated in the
      same commit.
- [ ] No public-surface change (grpc contract, plugin API, config sections)
      — otherwise it's a feature, use that bar.

**World-model schema change:**
- [ ] Everything in `/world-schema-migration`: both ladders appended, heads
      equal, locks regenerated and committed, schema-version test pins
      bumped, upgrade-path test from a legacy DB passes.

**Dashboard / UI change:**
- [ ] `python -m maverick.a11y_audit --ci` exits 0.
- [ ] Tests use the Origin-header + tmp-DB pattern (trap 15).
- [ ] New assets covered by package-data globs (trap 16).
- [ ] Copy matches the enterprise/liquid-glass voice of the surrounding
      pages — no dev jargon in user-facing strings.

**Docs change:**
- [ ] Links resolve (docs.yml runs `mkdocs build --strict` only after merge
      — a broken link ships before it's caught, so check relative paths).
- [ ] FEATURES.md stays present-tense and code-grounded; roadmap items live
      in ROADMAP.md until they ship.

## 7. When uncertain — the escalation ladder

**Tier 0 — never ask, just follow the code:** naming, file placement, idiom
choice, test structure. The answer is the nearest existing example; go find
it (`grep`, then copy its shape). Asking these reads as not having looked.

**Tier 1 — investigate before anything else:** any red gate or failing test.
You must be holding the exact failing command + its output before you form a
theory. Every gate is a readable Python module in `maverick/` — when a gate
fails and the message isn't enough, read the gate's source; it IS the spec.
Never weaken, skip, or `|| true` a gate to get green.

**Tier 2 — proceed, but state the assumption:** ambiguity where one reading
is clearly the maintainer's intent (matching every convention in §3) and the
cost of being wrong is a small rework. Write "Assumption: X, because Y" in
the PR body so it's reviewable.

**Tier 3 — stop and ask (one-way doors and scope changes).** Ask with a
specific question, your recommendation, and the evidence — never an
open-ended "what should I do?":
- Anything requiring `--force` on a ratchet, or the DESIRE to edit a
  released migration, evaluator anchor, or `maverick.v1` proto field.
- Destructive schema changes (DROP/RENAME) — the governance gate blocks
  them; wanting one means the design needs maintainer sign-off.
- New top-level dependency, license change, or anything that alters the
  security posture (auth exemptions, fail-open/closed seams, sandbox
  backends, secrets handling).
- A reviewer comment with two defensible readings, or a fix that requires a
  large-scale refactor to land honestly.
- Deleting or rewriting anything you didn't create whose contents contradict
  the task's description of it — surface the contradiction instead.
- Anything touching pricing, licensing, legal, pitch, or benchmark claims.

**Tier 4 — hard stops (no exception, no matter who asks):** fabricating
numbers, bypassing Budget, hard-coding model ids, committing real secrets,
merging on red, disabling a safety gate "temporarily".

## 8. PR titles & commits

`lint-pr-title` enforces Conventional Commits: allowed types `feat fix chore
docs test refactor perf ci build style revert` (scope optional), and the
subject must start with a LETTER — `feat: 2027 ...` and `feat: "What..."`
both failed real PRs; write `feat: add the 2027 ...`. Commit bodies: why,
not what; end functional commits with a `Tests:` line.

## 9. Kernel rules (unchanged, still enforced)

1. Kernel runs WITHOUT the shield — never require `agent-shield`; fail open
   with a warning (seam: `maverick/shield_policy.py`).
2. Never hard-code models — role → model resolution only (§5 trap 7).
3. Budget caps are not optional — never bypass `budget.check()`.
4. All shell through `sandbox.exec()` (CI greps for violations).
5. No new top-level deps without a config knob.
6. The wizard is the UX source of truth.
7. Style: surgical diffs; match existing style; no speculative abstractions;
   state assumptions; tests first for fixes/validation.

## 10. Verified-failure log (why these rules exist)

| Finding | Consequence encoded above |
|---|---|
| Bare `pytest` = 173 phantom collection errors | Trap 1 |
| `cmd \| tail` masked a collection abort | Trap 2 |
| 5 broken imports hid 9,000 green tests; pyo3 panic hid `_cffi_backend` | Trap 3 |
| Fresh Debian container: post-create dies on distro cryptography (2026-07-07) | Trap 4, §2 |
| Budget test died on a cap it never mentioned | Trap 8 |
| Bare `import tomllib` broke three PRs | Trap 10 |
| Consequence Engine shipped unreachable (knob, no wizard step) | Trap 23 |
| `templates/*.js` missing from package-data 500'd built apps only | Trap 16 |
| `feat: 2027 …` / `feat: "What…"` failed lint-pr-title | §8 |
| Bugbear flipped ON while docs said OFF | Prime directive, §4 |
