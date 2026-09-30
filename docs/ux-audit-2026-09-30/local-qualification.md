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
| `cfa16bfc6659b59d4e855beb024914409cad44a7` | Ordinary Python run in progress | Immutable checkout, matching-source virtual environment, six file-distributed workers, short private pytest path and short workspace native scratch path. |

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
The five independent current-code findings from PR2530 remain reported in
`concurrent-audit-comparison.md`, separate from the original 195-entry register.
