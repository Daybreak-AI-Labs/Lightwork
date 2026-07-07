---
name: preflight
description: Run every gate Lightwork CI will run, locally, in fastest-failing order — before any push or PR, after any multi-file change, or to reproduce a red CI locally. Covers all 16 lint-job steps (pre-push-lint.sh only covers 10), the test matrix, and audit-job parity.
---

# Preflight: make this branch CI-green before it leaves the machine

CI here has 7 jobs and 16 lint-gate steps; a push that skips any of them
costs a full CI round-trip (~15 min) per miss, and the bespoke gates
"repeatedly failed CI *after* a green local run" (pre-push-lint.sh header).
This skill runs everything, cheapest-first, so the first failure surfaces in
seconds, not minutes.

Total cost when green: ~12 minutes (static ≈1 min, suite ≈10 min at 15k+
tests — don't assume a hang).

## Phase 0 — toolchain sanity (skip if `maverick version` works)

```bash
maverick version >/dev/null 2>&1 || {
  # Fresh Debian containers: pip cannot upgrade distro cryptography 41.x
  # (no RECORD file); core floors >=44.0.1, so post-create dies without this.
  pip install --ignore-installed 'cryptography>=44.0.1'
  bash .devcontainer/post-create.sh
}
cd "$(git rev-parse --show-toplevel)"   # every command below assumes repo root
```

Also install what the lint/audit jobs need but post-create doesn't ship:
`pip install 'detect-secrets>=1.5' 'bandit>=1.7'` (first run only).

## Phase 1 — static gates, in CI order

`bash scripts/pre-push-lint.sh` runs the first 10 (ruff, vulture, tomllib
grep, shell=True grep, detect-secrets, plugin_matrix, deprecations, grpc
contract, a11y_audit, schema_migrations). **`git add -A` first — the secret
scan only sees tracked files.**

Then run the 6 gates the script does NOT cover:

```bash
maverick domains-lint --ci
python -m maverick.failure_policy --ci
python -m maverick.migration_governance --ci
python -m maverick.evaluator_evolution --ci
python -m maverick.control_data_plane_e2e --ci
python -m maverick.control_data_plane_soak --ci
```

Run each command directly — never `cmd | tail` (that reports tail's exit
code and has masked a collection abort here; use `${PIPESTATUS[0]}` if you
must pipe).

## Phase 2 — the suite

```bash
python3 -m pytest -q      # from repo root; NEVER bare `pytest` (uv/pipx shim trap)
```

Scope additions by what the diff touches:
- `sdks/plugin-ts/**` → `cd sdks/plugin-ts && npm install && npm test`
- `benchmarks/**` or eval harness → `python3 -m pytest -q benchmarks/test_eval_smoke.py`
- world-model postgres backend → the `postgres` CI job needs a live pgvector
  DB (`MAVERICK_PG_DSN=...`); if none is available locally, SAY SO in the PR
  test plan instead of claiming full coverage.
- `packages/maverick-shield/**` → `python -m maverick_shield.redteam`
- `deploy/docker/**` or entry points → the docker job smoke:
  `docker build -f deploy/docker/Dockerfile -t maverick:ci . && docker run --rm maverick:ci --help`

Collection-error triage (do this BEFORE judging any test result):
1. pytest aborts the whole run on ONE collection error — 5 broken imports
   once hid 9,000 green tests. Fix imports first.
2. A `pyo3_runtime.PanicException` at collection hides the real error on the
   captured-stderr line above it (canonically `No module named
   '_cffi_backend'` → `pip install cffi`).
3. 100+ simultaneous collection errors = you ran bare `pytest` from a shim
   venv, not a real breakage.

## Phase 3 — audit-job parity (CI blocks on these now)

```bash
python -m bandit -q -r packages apps -lll -ii -x '*/tests/*,*/test_*.py'
```

- Bandit finding that is by-design → `# nosec <check-id>` with a short
  justification comment; never blanket-disable.
- If you ADDED or bumped a dependency, also run `pip-audit` (any known CVE
  fails CI) and check it isn't strong-copyleft (GPL/AGPL/SSPL — the license
  gate fails those).

## Phase 4 — commit & PR hygiene

- Title/commit subject: Conventional Commits type (`feat fix chore docs test
  refactor perf ci build style revert`) AND subject starts with a LETTER —
  `feat: 2027 ...` and `feat: "What..."` both failed real PRs.
- `git diff --stat` review: only files the task required. Reformatted
  neighbors, "improved" adjacent code, stripped `# noqa` = revert them.
- Functional commits carry their regression test in the same diff and end
  the body with a `Tests:` line.
- Fill `.github/PULL_REQUEST_TEMPLATE.md` sections (What/Why/How/Test
  plan/Checklist) honestly — if something is unverifiable in this
  environment (no API key, no PG), state it; never claim an unrun check.

## Failure playbook

| Symptom | Cause | Fix |
|---|---|---|
| 173 collection errors instantly | bare `pytest` shim | `python3 -m pytest` |
| pyo3 PanicException at collection | distro cryptography lacks `_cffi_backend` | `pip install cffi`; read the stderr line above the panic |
| post-create dies at step 1 | distro cryptography 41.x, no RECORD | `pip install --ignore-installed 'cryptography>=44.0.1'` first |
| detect-secrets fails on your new test | fake key/token hash not in baseline | `# pragma: allowlist secret` on that line (false positives only) |
| shell=True grep fails on a comment | the literal string trips it even in comments | reword the comment; real calls go through `sandbox.exec()` |
| migration_governance fails after schema work | lock not regenerated | see `/world-schema-migration` — regen + commit `migrations.lock.json` |
| evaluator_evolution fails on new anchor | anchor not locked | `python -m maverick.evaluator_evolution --regen`, commit the lock; NEVER `--force` without maintainer sign-off |
| grpc contract fails | proto removal/rename/renumber | additive only; breaking = new `maverick.v2`; additive = `--regen` + commit golden |
| deprecations --ci fails | a `remove_in` came due | delete the deprecated path AND its REGISTRY row together |
| a11y_audit fails | template regression (alt/label/lang/heading) | fix the template; don't exempt |
| test dies on a Budget cap it never set | `record_tokens` enforces ALL caps | raise every cap the test will cross |
| dashboard test 403s | missing Origin header in no-token CSRF mode | `TestClient(app, headers={"Origin": "http://testserver"})` |
| async test "passes" without running | strict asyncio mode | add `@pytest.mark.asyncio` |

## Done means

Every phase-1 command exited 0, the suite reports `0 failed, 0 error` with
no new skips, phase-3 is clean (or its gaps are declared in the PR), and the
title/diff pass phase 4. Only then push. If any gate stays red and the fix
would mean weakening the gate itself — stop; that needs the maintainer
(CLAUDE.md §7, Tier 3).
