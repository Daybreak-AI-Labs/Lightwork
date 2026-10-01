# Reviewing the consolidated audit draft

This is one draft PR, preserving the original employee-companion handoff.
The [register](implementation-tracker.json) retains all 195 audit IDs and their
individual implementation and acceptance evidence; the [checklist](implementation-checklist.md)
is its shorter index. The original 24 fields per entry remain in
[source-backlog.json](source-backlog.json). The five separately approved
security fixes have their own [register](additional-security-fixes.json).

There are 190 implemented entries awaiting final acceptance and five explicit
qualification entries:
LW057, LW149, LW154, LW155 and LW184. These counts do not
mean that every external service, native platform, language or distribution is
qualified. The [local qualification record](local-qualification.md) identifies
failed/incomplete runs, later repairs and exact-source results.

Browser acceptance reopened LW-064, LW-091 and LW-099. Their widget, native role
form and workspace recovery repairs are now integrated, and all 18 focused
acceptance cases pass at `1719f2f`. The complete 108-case browser and 1,711-case
JavaScript cohorts also pass there. A subsequent short-viewport widget boundary
was reproduced and repaired; [all seven updated widget cases pass](widget-short-viewport-88cd66f.md).
[All 26 fresh static/build gates pass](static-build-qualification-57a027a.md).
Final full Python qualification remains in progress.

The latest complete ordinary run still has one
[unresolved transport timeout](transport-timeout-investigation-95aa61e.md).
Its focused passes do not replace fresh integrated qualification. The
[skip identity ledger](skip-reconciliation-final07.md) distinguishes separate
dependency-cohort passes from tests that remain unavailable.

The user's separate PR #2531 has a confirmed conflicting version-42 schema
migration on both database backends. The
[read-only comparison](concurrent-pr2531-overlap.md) records the exact source
heads and the migration/approval contracts that must be reconciled before
combining the drafts. That separate work has not been imported or changed.

## Changes that affect existing installations

- **Employee content:** administrator policy authority no longer grants automatic
  access to another employee's task/chat content. Ownership is preserved.
  Exceptional access is disabled by default; when enabled through Settings or
  the installer, Account/Users links to exact-resource requests with independent
  approval, expiry and acknowledged audit receipts. It grants reads, not
  mutation or publication. See [employee content access](../employee-content-access.md).
- **Legacy records:** ambiguous deployment-global feedback and UX records remain
  retained outside ordinary workspace reads. Unknown ownership is not guessed
  and legacy content is not silently reassigned.
- **Workflows:** deployment-secret expressions now refuse before effects.
  Existing definitions remain readable and explain their migration to
  authorized scoped Connections. Follow [credential migration](../flow-credential-migration.md);
  deployment model credentials and provider configuration are unchanged.
- **Workspace retirement:** deleted identities stay reserved, including through
  restore, rollback and interrupted recovery. A replacement workspace needs a
  fresh identity; retained files and memberships are not inherited by reuse.
- **Paid knowledge rebuilds:** supported priced routes require current cost
  review and authority, durable usage acknowledgement and explicit recovery of
  ambiguous attempts. Unpriced or expired-price routes refuse. There is no
  automatic paid retry. See [hosted qualification](hosted-reindex-qualification-2026-10-01.md).

## Product review paths

Start with task chat and My Work, then Account/Preferences and My Data,
Connections, Knowledge/Facts, workflow review, projects and reporting. Each
register row points to its native UI, service, current-authority checks and
recovery behavior. [The operation matrix](conversation-operation-matrix.md)
shows which typed Copilot actions use those same services. Companion chat has
its own [bounded capabilities](../employee-companions.md); it does not
inherit every ordinary-task action.

Language catalogs remain visibly draft/partial until human review; English is
the qualified complete-body locale. Native/IDE builds, real provider consent,
signed distribution and distributed failure scenarios retain their explicit
qualification limits. No merge, deployment or production migration has been
performed by this work.
