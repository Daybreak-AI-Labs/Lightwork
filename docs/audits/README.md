# Audit archive

Point-in-time audits, ledgers and reviews of the platform, kept for the record.
They are not trackers: a finding listed in one of them may already be fixed. The
live list of what is still open is [docs/OPEN_ITEMS.md](../OPEN_ITEMS.md).

| Document | Date | What it is |
| --- | --- | --- |
| [USER_EXPERIENCE_AUDIT.md](USER_EXPERIENCE_AUDIT.md) | 2026-09-30 | User-experience audit of the September 30 baseline (`6daa3165`), with "current branch" notes under some findings. |
| [PLATFORM_IMPROVEMENT_AUDIT.md](PLATFORM_IMPROVEMENT_AUDIT.md) | 2026-09-07 | Platform improvement audit of base commit `2d75929`; its W1/W2 finding IDs are cited from code comments and tests. |
| [INFRA_AND_POLICY_DECISIONS.md](INFRA_AND_POLICY_DECISIONS.md) | 2026-09 | The 30 infrastructure and governance items the platform audit left for a decision rather than a code fix. |
| [QA_FINDINGS.md](QA_FINDINGS.md) | 2026-09-01 | Test-backed remediation ledger for the user-journey audit and the follow-up security and continuity pass. |
| [ADVERSARY_COUNCIL_REVIEW.md](ADVERSARY_COUNCIL_REVIEW.md) | 2026-07-13 | Ten-seat adversarial review of base commit `f997f17`; only findings that survived an independent refutation attempt. |
| [CODE_QUALITY_AUDIT.md](CODE_QUALITY_AUDIT.md) | 2026-06-27 | Full-codebase code-quality review. |
| [PUNCHLIST_RESOLUTION.md](PUNCHLIST_RESOLUTION.md) | 2026-06-27 | Disposition of every punchlist item from the code-quality audit. |
| [STRESS_TEST.md](STRESS_TEST.md) | 2026-06-26 | Platform stress test under adversarial and inconsistent environments. |
| [FIXES.md](FIXES.md) | 2026-06 | Fix backlog status board compiled from a full read-through of the codebase. |
| [LAUNCH_AUDIT.md](LAUNCH_AUDIT.md) | undated | Public notice that a pre-launch hardening audit was performed; its findings are tracked privately. |

The dated client- and customer-readiness reviews in this directory
(`client-readiness-follow-up-2026-09-13.md`, `client-reliability-2026-09-13.md`,
`customer-readiness-2026-09-14.md`, `customer-readiness-consolidation-2026-09-23.md`
with its `.json` data, `customer-readiness-follow-up-2026-09-23.md`) and the
`evidence/` captures are archived the same way.

Code comments and tests cite findings by document and ID, for example
`docs/audits/PLATFORM_IMPROVEMENT_AUDIT.md W2-1`.
