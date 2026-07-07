---
name: world-schema-migration
description: Safely change the world-model schema across BOTH ladders (SQLite world_model.MIGRATIONS and the Postgres backend) without tripping the migration-governance ratchet. Use whenever touching world_model.py's SCHEMA/MIGRATIONS/SCHEMA_VERSION, world_model_backends/postgres.py, or when migration_governance/schema_migrations gates go red.
---

# World-model schema changes: the ratchet ritual

The world model is the platform's Operating Record; its migrations are
governed by an integrity ratchet: **released migrations are immutable,
destructive statements are blocked, and two independent backends must agree
on the schema head.** Schema versions are bumped often here (v25 → v27 in
recent history), and every bump that skips a step below is a red
`migration_governance --ci` or a broken upgrade path for real installed
databases.

## The two ladders (they are shaped differently — don't mirror blindly)

| | SQLite (canonical) | Postgres |
|---|---|---|
| File | `packages/maverick-core/maverick/world_model.py` | `packages/maverick-core/maverick/world_model_backends/postgres.py` |
| Shape | `MIGRATIONS: dict[int, list[str]]` — keys are NOT authored in numeric order (v11 appears before v2); applied sorted | `MIGRATIONS: list[tuple[int, list[str]]]` — append at the end |
| Head | `SCHEMA_VERSION = <N>` set MANUALLY at the top | `_PG_SCHEMA_VERSION = MIGRATIONS[-1][0]` — derived automatically |
| Empty entries | `[]` values are intentional placeholders (the table lives in the idempotent `SCHEMA` string) | n/a |

The governance gate fails if the two heads diverge, or if a Postgres version
has no SQLite counterpart (base v1 excepted).

## Step 1 — decide whether you even need a migration

- Brand-new table that has NEVER shipped → it can live in the idempotent
  `SCHEMA` (`CREATE TABLE IF NOT EXISTS …`) with an empty `[]` migration
  placeholder for its version, following the existing pattern.
- Any change to data that EXISTS on installed machines (new column, new
  index, backfill) → a real migration on both ladders.
- Purely in-memory / no persisted shape change → stop; no migration.

## Step 2 — author the migration (additive only)

Let `N = SCHEMA_VERSION + 1`.

1. SQLite: add `N: ["ALTER TABLE … ADD COLUMN …", …]` to
   `world_model.MIGRATIONS` and bump `SCHEMA_VERSION = N` (world_model.py
   top).
2. Postgres: append `(N, _<NAME>_MIGRATION)` to the postgres `MIGRATIONS`
   list (its head updates itself). Keep column/semantic parity with the
   SQLite statements — see the `v27` cache-token entry for the paired shape.
3. **Never** DROP, RENAME, or edit an existing version's SQL — the
   governance gate blocks destructive statements in NEW migrations too. If
   the design genuinely needs a destructive change, stop: that is a Tier-3
   escalation (CLAUDE.md §7) requiring maintainer sign-off, not a workaround.
4. Keep migration SQL classifiable: plain `ALTER TABLE ADD COLUMN`,
   `CREATE INDEX`, `CREATE TABLE`, simple backfill `UPDATE`s.
   `schema_migrations --ci` fails on SQL shapes it cannot classify.

## Step 3 — regen the lock (the part everyone forgets)

```bash
python -m maverick.schema_migrations --ci        # SQL-shape lint first
python -m maverick.migration_governance --ci     # expect FAIL: new unlocked version
python -m maverick.migration_governance --regen  # rewrites migrations.lock.json
python -m maverick.migration_governance --ci     # now green
git add packages/maverick-core/maverick/migrations.lock.json
```

Never hand-edit `migrations.lock.json`. If `--regen` complains about a
CHANGED released migration, you edited history — revert the edit; do not
force the lock.

## Step 4 — update the version pins (recurring, easy to miss)

Test pins reference the concrete head and break on every bump — this repo
has a whole genre of `test: bump the schema-version pins to vNN` commits.
Find them all:

```bash
grep -rn "SCHEMA_VERSION\|schema_version\|v$OLD\b" packages/maverick-core/tests/ | grep -iv "\.pyc"
```

Known pin sites: `test_world_model_migrations.py` (legacy-DB → HEAD upgrade
walk), `test_migration_governance.py`, `test_postgres_world.py`, prewarm /
cache expectation tests (`test_cache_prewarm.py` asserted concrete schema
contents in past bumps), and `maverick version` output tests if any assert
`schema: vNN`.

## Step 5 — prove the upgrade path, not just the fresh path

- Fresh DB: `python3 -m pytest -q packages/maverick-core/tests/test_world_model.py`
- Upgrade path: `test_world_model_migrations.py` builds a LEGACY schema on
  disk and walks it to HEAD — extend it (or the relevant table's test) with
  an assertion that data written before your migration survives it.
- New column semantics get their own regression test in the module's test
  file (e.g. spend columns → the budget/costs test asserts round-tripping).
- Postgres: the live test (`test_postgres_world.py`) needs
  `MAVERICK_PG_DSN`; without a local pgvector instance, run everything else
  and state in the PR test plan that the PG leg runs in CI's `postgres` job
  — do not claim you ran it.

## Step 6 — ship

- One commit: schema change + both ladders + lock + test-pin bumps + the new
  regression test. Subject: `feat: …` / `fix: …` with body explaining WHY
  the schema grew and a `Tests:` line.
- Run `/preflight`. The gates that specifically judge this work:
  `schema_migrations --ci`, `migration_governance --ci`, plus the full suite
  (the upgrade-walk tests are in it).

## Failure playbook

| Gate says | It means | Do |
|---|---|---|
| checksum mismatch on version K < N | you edited a released migration | revert the edit; new behavior goes in version N |
| destructive statement (DROP/RENAME) | blocked by design | Tier-3 escalation; usually the answer is an additive column + deprecation of the old one |
| backend head mismatch | one ladder bumped, other not | append the missing counterpart; heads must be equal |
| PG version without SQLite counterpart | postgres-only migration | add the SQLite twin (or `[]` placeholder if SCHEMA covers it) |
| unclassifiable SQL | exotic statement shape | rewrite as plain ALTER/CREATE/UPDATE forms |
| suite fails on schema_version assertion | stale test pin | Step 4 — grep for the old version number |

## Done means

Both ladders share head N, `migrations.lock.json` is regenerated and
committed, every stale pin is bumped, a legacy DB walks to N with data
intact, and `/preflight` is green.
