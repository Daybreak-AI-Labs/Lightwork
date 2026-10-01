# Local qualification checkpoints

Local testing is authorized. These results replace the earlier testing deferral;
they do not certify an installed release or live provider integration. All
provider, identity-provider and billing boundaries use synthetic data or mocks.
No deployment configuration or credentials were changed.

## Full-suite checkpoints

| Checkout | Result | Meaning |
| --- | --- | --- |
| `b0a7eea` | 36,262 passed, 3,102 failed, 195 errors, 551 skipped | Failed initial ordinary Python qualification. Retained as failure evidence, not acceptance. |
| `d822db99ee48aab4fdded47fc799486e8e5ea44f` | All 1,594 dashboard JavaScript cases passed | Complete JavaScript run at this checkpoint. Later controller changes require integrated requalification. |
| `9bea7d4e4c751b022cf5f9d6b261c8af6f025d6c` | Ordinary run stopped during collection | Its long native temporary path was replaced after Windows file tests demonstrated a bounded environment requirement. No pass/fail total is claimed. |
| `cfa16bfc6659b59d4e855beb024914409cad44a7` | Incomplete; stopped after worker crash and scheduler stall at99% | Read-only stack inspection showed all workers waiting, not executing tests. No final JUnit or pass/fail total exists; progress dots are not acceptance. The actual assertion failures remain unresolved. |
| `f036cef90a6d6fd1ddaa3f76bd857b9ca9bd4c3e` | All1,680 dashboard JavaScript tests passed across176 files | Zero failures/skips/cancellations. Exact manifests and source boundaries: dashboard-javascript-qualification.md. |
| `12ff6b8ad9e42f73a13467c3514f9687ba57465f` | Incomplete: 5,669 passed, 10 skipped, two worker crashes | Run ended after 837.37 seconds with both settings catalog cases losing their workers. Remaining cases were not executed. Streamed reports and JUnit retain the partial result; this is not a full-suite pass. |
| `29aee7e75b6d31012f087ec725456eec20e206af` | 39,763 passed, 219 failed, 555 skipped | Complete failed ordinary run; no worker losses or collection errors. Both former crash cases passed unchanged. Later repairs require fresh qualification. |
| `2c1e8a888eee011a2851836079b57c9929b8d745` | Ordinary: 39,987 passed, 17 failed, 558 skipped; secure incomplete | Ordinary completed in 2,736.35 seconds, with zero errors or worker losses. Its 17 failures belong to two test modules repaired in `a726d51`; the complete two-module cohort passed 46 cases with two POSIX skips per posture. Secure ended after 7,581 passes, 108 skips and one worker crash during the timed stack dump in the 1,000-round self-harness model test. Both secure switches were explicit. No complete secure pass is claimed. |

The initial failures are not dismissed as a single fixture problem. Production
repairs include exact ownership for local schedules, employee content reads in
independent reviews, complete private-record erasure, immediate learning stop,
explicit recovery-authority classification, restored safe governance metadata,
and private UI cleanup after access loss. Updated tests exercise the actual
current revision/review/owner boundaries and preserve denial assertions.

## Focused evidence

- All 70 distinct installed-Chrome journeys passed across the complete browser
  run and targeted repairs. `browser-qualification.md` records the five-role
  invitation, approval, export and departure journey and the exact limits.
  This is not a single final-commit 70-case run.
- Core privacy: all 205 cases passed; two existing symbolic-link cases require
  Windows link-creation privilege and are explicitly skipped. The full harness
  and targeted follow-ups are recorded in the identity qualification documents.
- Dashboard controls: the 17-family run passed 498 cases; its one outdated owned
  approval-history expectation was corrected and passed separately. The
  independent content-read and employee-privacy regressions passed 17 cases.
- Reviewed task/schedule operations passed 186 distinct cases at their source
  checkpoint; conversation qualification records overlap with earlier runs.
- Native regression group: 259 cases passed in each of ordinary and secure
  default modes. Two link-privilege skips remain explicit in each run.
- The 75 native-file cases covering spreadsheets, document processing, fair
  queues and retained-custody recovery pass with native scratch under the short
  workspace `.t` directory. External scratch reproduced `lxml` access errors
  under pytest; excessively long workspace paths reproduced Windows cleanup
  limits. No library or file-safety guard was bypassed.
- Ruff and Vulture pass at `0ac794e`; subsequent changes still require the final
  static gate. Earlier offline contract checks and eight package wheels passed
  at their documented checkpoint and will be repeated after integration.

Focused counts may overlap and must not be summed as a unique test total. Full
ordinary, serial heavy-concurrency, secure-default, browser, contract and build
results remain pending on the consolidated final source.

## Qualification boundaries

Real provider/IdP consent and accounting, native Apple/Android SDKs and devices,
signed release trust and clean-machine installation, installed IDE behavior,
dedicated PostgreSQL/distributed failure scenarios, and human language review
remain separate evidence requirements. Local JetBrains and Zed builds are
recorded in `native-build-evidence.json`; they are not distribution approval.
The five independently traced PR2530 findings were separately approved.
`additional-security-fixes.json` records implementation, regression evidence and
tenant restore-boundary regression evidence, separate from the195-entry register.

The tenant restore repair preserves namespace claims and deletion tombstones
through publication, rollback, older interrupted journals and concurrent claims.
Its eight-file secure-default cohort passed 219 tests with two Windows
symbolic-link privilege skips. All 13 new public-restore models passed. The
ordinary cohort's one inventory omission was corrected and its four-case gate
passed again; independent replay of the original restore bypass remains part
of final qualification. Failed restores may conservatively retain newly
published identity barriers; they never release those identities.

The two settings worker losses were investigated separately from assertion
failures. An unchanged isolated governance case passed in 196.03 seconds,
including 135.656 seconds rendering settings groups. Repeated authenticated
credential-key permission checks are a measured hot path. The native access
violation seen during a diagnostic dump has not been independently reproduced;
its cause remains unresolved. The batch-local decryption repair passed 88
configuration/crypto cases with two Windows privilege skips. Both unchanged
exhaustive settings cases passed serially and in parallel; the secure parallel
run passed those two and four new regressions. Timeouts and access assertions
were retained. Details: settings-config-read-qualification.md.

At cfa16bf,15 of16 offline contract gates and all8 package wheels passed. The
remaining constraint check required pip26.2.1 rather than the installed25.0.1.
After the incomplete suite was stopped, only that pinned local tool was upgraded;
constraint closure then passed with91 exactly pinned external distributions.
Final-source contracts/builds remain pending.

At `29aee7e`, Ruff, Vulture, all 16 offline contracts and all eight wheels passed.
Every wheel passed archive integrity checks, and the core configuration source
matched the exact Git export. Source and wheel hashes plus individual check
results are in static-build-qualification.json. Later source changes and the
full runtime phases remain separately tracked; these are not release approval.

Optional DuckDB qualification found a remaining LW-095 raw SQL content-read
side path. Commit `59ab6ea` retains global query permission while restricting
materialized task rows to the exact request owner. The same three real-DuckDB
regressions failed on the prepatch source, returning other employees' rows,
and passed after repair in ordinary and secure modes. This belongs to the
original approved privacy policy; it is not a sixth PR2530 finding.

## Complete ordinary checkpoint, 2026-10-01

The frozen `29aee7e75b6d31012f087ec725456eec20e206af` ordinary run
completed in 2,892.21 seconds: **39,763 passed, 219 failed, 555 skipped**,
with no collection errors or worker losses. Both previously crashing Settings
cases passed unchanged. This is a failed qualification result, not acceptance.
The exact source, JUnit, streamed failure details and process result are retained
under `audit/qualification-final-02-ordinary.*` in the local workspace.

Repairs made after that frozen source include SQL analytics owner filtering,
bounded translation delivery, named identity dispatch for code notes, and a
Preferences rendering fix for the `source` interpolation placeholder. Each
has native regressions; no response-size, access-denial or revocation assertion
was relaxed. Standalone page fixtures now assemble the same template context as
the installed application. Historical migration fixtures explicitly construct
the historical schema instead of writing it through the current schema writer.
Current-revision save, reviewed erasure and source-lineage tests use their real
native contracts. Remaining failures continue through focused review and a
fresh consolidated run; counts are not inferred from patch inspection.

Dashboard-only secure cohorts must explicitly set both
`MAVERICK_SECURE_DEFAULT=1` and `MAVERICK_TEST_SECURE_DEFAULT=1`. The latter is
consumed by the core test fixture and alone does not establish dashboard-only
production posture. The harness now sets both. Earlier dashboard-only labels
without this evidence are ordinary/posture-unspecified checks; corrected
cohorts and the final full secure run are tracked separately.

Final per-criterion review reopened LW-122: the local-only bounded reindex
implementation does not complete the existing hosted-embedding path. Supported
hosted cost admission and ambiguous paid-outcome recovery are active work.
The same review reproduced omitted custom-theme semantic colours and hidden
mobile Close-button focus after desktop resize; their repairs are in `5bc069b`,
after the `2c1e8a8` runtime checkpoint. New local and browser regressions qualify
those changes separately before another consolidated freeze.
