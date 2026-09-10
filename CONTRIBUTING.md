# Contributing to Lightwork

Thanks for being here. Lightwork is built by accretion -- every commit
should make the foundation stronger without forcing a rewrite. Read
these guidelines before opening your first PR.

> **Note:** Lightwork is now proprietary, commercially licensed software (see
> [`LICENSE`](./LICENSE)). External contributions require a signed CLA — see
> "License & contributions" below.

## Quick start (dev setup)

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd maverick
# Core + the full dev/test toolchain (pytest, ruff, pre-commit, and the
# cross-package runtime deps the suite imports) in one shot:
pip install -e './packages/maverick-core[dev]'
# Sibling packages are import-time deps of the suite (installed --no-deps;
# their runtime deps come from [dev] above at the floors CI enforces):
pip install --no-deps -e ./packages/maverick-shield
pip install --no-deps -e ./packages/maverick-channels
pip install --no-deps -e ./packages/maverick-dashboard
pip install --no-deps -e ./packages/maverick-mcp
pip install --no-deps -e ./apps/installer-cli
pre-commit install

pytest -q
```

Make sure `maverick --help` works and `maverick doctor` prints a
status table. `pre-commit install` wires the lint gate (below) to run on
every commit, so you catch a ruff failure locally instead of in CI.

## Project shape

```
packages/
  maverick-core/       Python agent kernel (recursive swarm, world model,
                       multi-provider LLM dispatch, skills, sandboxes)
  maverick-shield/     Agent Shield integration with builtin fallback rules
  maverick-channels/   12 channel adapters (Telegram, Discord, Slack, ...)
  maverick-dashboard/  FastAPI local web UI (goals/skills/facts/spend)
  maverick-mcp/        Model Context Protocol server (stdio JSON-RPC)
apps/
  installer-cli/       Interactive Python TUI wizard
  installer-desktop/   Tauri-based native GUI installer
deploy/
  docker/  vps/  desktop/
docs/                  User-facing guides
benchmarks/            Long-horizon eval suite + example skills
```

New capability → new package under `packages/`. New entry point → new
app under `apps/`. The wizard MUST learn to enable any new capability,
or non-technical users can't reach it.

## House rules

1. **Surgical changes.** Don't "improve" adjacent code. Match existing
   style even if you'd do it differently.
2. **Shield wired or it doesn't count.** If you add a new tool / sink,
   it goes through `ctx.shield.scan_tool_call()` before execution.
3. **Budget caps are non-negotiable.** Every long-running path must
   respect `Budget`. No bypassing `budget.check()`.
4. **Sandbox-mediate all shell.** Never call `subprocess.run` from a
   tool directly -- go through `sandbox.exec()`.
5. **Per-role model choice is user-controlled.** Never hardcode a model
   in the agent loop. Read from `maverick.config.get_role_model(role)`.
6. **Fail-open with a warning, never silently.** A broken scanner or
   missing optional dep must log loudly.
7. **Defaults belong in code; overrides in `~/.maverick/config.toml`.**

## Tests

- Every package has a `tests/` directory with `test_*.py` files.
- Use the `FakeLLM` fixture in `packages/maverick-core/tests/conftest.py`
  for any test that touches the agent loop -- never burn API credits in CI.
- Tests run in CI on Python 3.10/3.11/3.12 as three disjoint file-level
  slices per interpreter (`--shard N/3`, defined in the root `conftest.py`),
  each under pytest-xdist with one worker per core and tests grouped by file
  (`-n auto --dist loadfile`). Run the whole suite the same way locally --
  `python -m pytest -q -n auto --dist loadfile` -- and it takes minutes
  instead of the half hour it takes serially. Tests must therefore be
  file-independent: no fixed TCP ports, no shared paths outside `tmp_path`,
  nothing that assumes another file ran first or after. xdist orders files
  by size, not alphabetically, so "it only leaks into files that run later"
  is not a defence; the root conftest fails any test that leaves a
  `maverick.*` package attribute bound to a re-imported module copy, the
  leak that took nine unrelated tests down the first time. A test whose
  *own* concurrency contends on SQLite (the queue soak, the discovery
  estate runner) carries `@pytest.mark.heavy_concurrency`; CI runs those in
  a short serial phase after each slice's parallel pass so they never share
  a two-core box with a second xdist worker.

## Continuous integration (read before merging)

**A PR is not mergeable until every required check is green.** `main` is
protected by the ruleset in `.github/rulesets/protect-main.json`: every
change lands through a pull request, through the **merge queue**, as a
**squash**, and only once every required check below has passed on the
queue's merge of the PR onto the current `main` (so "it was green on my
branch" and "main moved after I pushed" can no longer put a red commit on
`main`). Force-pushes and branch deletion are refused, history stays linear,
and nobody is on the bypass list. The ruleset requires **zero approving
reviews** only because the repository has a single maintainer, who cannot
approve their own PRs; the change-management policy wants an independent
approver, so the day a second maintainer exists, set
`required_approving_review_count` to 1 and `require_code_owner_review` to
true in the JSON and re-import.

The ruleset is applied in **Settings -> Rules -> Rulesets -> New ruleset ->
Import a ruleset** with that JSON file; edit the file and re-import to change
it, so the protection is reviewed like code. Two repository settings go with
it: **Settings -> General -> Pull Requests -> "Allow squash merging" -> default
commit message = "Pull request title"** (the "Default message" option
prefills the squash from every branch commit, which is how AI co-author
trailers reached `main`), and the **merge queue** appears on each PR as
"Merge when ready" once the ruleset is active.

The required checks (each must also trigger on `merge_group`, or the queue
can never merge -- `test_every_required_workflow_runs_in_the_merge_queue`
pins that):

| Check                              | What it runs                                                          |
| ---------------------------------- | --------------------------------------------------------------------- |
| `lint`                             | `ruff check .`, vulture, the tomllib/shell=True greps, detect-secrets, and every `python -m maverick.<gate> --ci` contract gate |
| `lint-pr-title`                    | Conventional-Commit PR title (see below)                              |
| `no-ai-attribution`                | `scripts/check_attribution.py` over the PR's commits + title/body, the merge-queue commit, and pushes to `main`: no AI co-author trailers, generator footers, assistant session links or assistant author/committer identities (a squash merge turns every distinct commit author into a `Co-authored-by:` trailer) (CLAUDE.md) |
| `test (3.10, 1/2/3)`               | the full `pytest` suite on the floor interpreter, three file-level slices, on every pull request |
| `test (3.11, 1/2/3)`, `test (3.12, 1/2/3)` | the same slices on the rest of the supported window (`test-window` in ci.yml): on a pull request the legs start, skip every step and report success within seconds; on the merge queue's commit and on `main` they run for real |
| `posture (1/2/3)`                  | the full suite again under the production security posture on 3.12, three slices. Gated the same way as `test-window`: it starts, skips and reports success on a pull request, and runs for real on the merge queue's commit and on `main` -- so the whole 3.12 + secure-defaults suite is proven at merge, not on every PR push |
| `harness-overhead`, `governance-frontier-offline` | the tracked benchmark manifests verify against the current sources (regenerate them when a control-tree source changes) |
| `audit`                            | OSV/pip-audit/bandit/SBOM across every dependency surface             |
| `release-cohort`, `redteam`, `eval-smoke`, `postgres`, `docker` | packaging cohort, shield red-team corpus, eval harness, Postgres backend, deploy image |
| `Windows process containment (Python 3.10/.11/.12)` | Job Object process-tree containment on Windows: 3.10 on every pull request, 3.11 and 3.12 on the queue and `main` |

Two recurring foot-guns, both enforced by CI:

1. **`ruff check .` runs on the whole repo, not just your diff.** An unused
   import you orphaned in another file fails lint for *everyone*. Run
   `pre-commit run --all-files` (or just `ruff check .`) before you push --
   `pre-commit install` makes this automatic on commit.
2. **`import tomllib` needs the 3.10 fallback.** `tomllib` is 3.11+ stdlib;
   importing it bare breaks the 3.10 leg of the matrix. Use the
   `try/except ModuleNotFoundError: import tomli as tomllib` pattern from
   `CLAUDE.md`.

**PR titles** must be Conventional Commits (`feat:`, `fix:`, `docs:`,
`refactor:`, `test:`, `chore:`, `perf:`, `ci:`) **and the subject after the
prefix must start with a letter** -- not a digit, quote, or backtick
(`feat: 2027 roadmap` fails; `feat: add the 2027 roadmap` passes).

## Adding a provider

1. Create `packages/maverick-core/maverick/providers/<name>_provider.py`
   with a class implementing `complete()` and `complete_async()` that
   accept Anthropic-format messages/tools and return `LLMResponse`.
2. Register in `packages/maverick-core/maverick/providers/__init__.py`
   under `get_provider_client` and `KNOWN_PROVIDERS`.
3. Add to the wizard catalog at
   `apps/installer-cli/maverick_installer/models.py`.
4. Add tests for any format-translation logic.

## Adding a channel

1. Subclass `Channel` in `packages/maverick-channels/maverick_channels/<name>.py`.
2. Wire it in `server.py` (`_wire_<name>` + `_WIRES` dict).
3. Add to the wizard `CHANNELS` list.
4. Document setup steps in `docs/deployment.md`.
5. If it needs a Python SDK, add to `pyproject.toml` optional extras.

## Adding a skill

Write a `SKILL.md` (see `benchmarks/example-skills/README.md` for the
schema) and put it in `benchmarks/example-skills/`. Users install with
`maverick skill install gh:...`.

## Commit style

- Conventional commit subject (`feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `chore:`).
- Body explains the *why*, not the *what*. The diff shows what.
- Reference the council finding or issue if applicable.

## Reporting bugs

Use the issue templates. Include `maverick doctor` output and the
relevant section of your `~/.maverick/config.toml` (with secrets
redacted).

## License & contributions

Lightwork is **proprietary** software (see [`LICENSE`](./LICENSE)). External
contributions are accepted only under the signed Contributor License Agreement in
[`CLA.md`](./CLA.md), which assigns the Licensor the rights needed to relicense the
contribution; contact the maintainers before opening a PR. By contributing, you agree your
contribution becomes part of the proprietary work owned by the Licensor. (The
former inbound=outbound-MIT terms do not apply to contributions made after the
relicense; versions previously published under MIT remain MIT for those
versions.)
