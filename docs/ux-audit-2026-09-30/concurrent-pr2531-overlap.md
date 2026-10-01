# Coordination with the separate teardown draft

This is a read-only comparison of PR #2528 at
`35b715d3455109b39c29f18bbdae8273c596ad0d` and the user's separate
[PR #2531](https://github.com/Daybreak-AI-Labs/Lightwork/pull/2531) at
`e2014c94d71a3e21f226e6fc461351ae9a022008`, against their common
`6daa3165ff5a844320ec970553d5ecdec7e396c4` baseline. No code from that PR was
imported, executed, merged or changed. Its author-reported tests are not evidence
for this draft. The [exact inventory](concurrent-pr2531-overlap.json) records
1,306 changed paths here, 2,129 there, and **85 shared paths**. Shared paths are
not automatically textual conflicts.

## A confirmed schema collision must be resolved before combining the drafts

Both backends were version 41 on the common baseline. This draft's SQLite and
PostgreSQL ladders now end at 46; PR #2531 ends at 42. **Both drafts assign different
statements and lock fingerprints to version 42:**

| Version | PR #2528 | PR #2531 |
| --- | --- | --- |
| 42 | Goal mutation receipt schema | Webhook subscriptions/delivery log and deadline-alert ledger |
| 43 | Fact tags | — |
| 44 | Personal alert preferences/ledger | — |
| 45 | History indexes | — |
| 46 | Exact goal connection selection | — |

The conflict is present in `world_model.py`,
`world_model_backends/postgres.py` and `migrations.lock.json`. The configuration
section changes in `migrate.py` also overlap, but that file does not define the
world database version ladder. Keeping either version-42 entry mechanically is
not a valid combined migration plan. Both the required tables and their
historical fingerprint meanings must survive, with upgrade tests for each
supported starting state on both backends.

Recommended coordination, if neither migration history has shipped: finish and
review this draft first, then rebase the separate draft and give its new schemas
the next unused version (currently 47), regenerate the lock through the existing
governance tool and qualify the combined upgrade. This is a recommendation, not
an authorized merge order or a performed renumbering. If either divergent history
has shipped, retain that history and design explicit compatibility transitions;
do not rewrite a released migration fingerprint.

## Behavioral overlap also requires review

- PR #2531 adds a default **30-second approval undo grace period**, delayed
  consumption and additional vote identities after undo in
  `safety/consent.py`, both world backends and dashboard approval handlers.
  This draft's existing approval outbox/current-card/reconciliation behavior
  must remain intact when those changes are combined.
- Its approval API additionally requires explicit acknowledgement for high and
  critical risks through `approval_decisions.py`. That is a request-contract
  change: native UI, API and authorized Copilot callers need compatible payloads,
  current authority and replay behavior. It is not adopted by this draft.
- Shared tenant registry, path binding and recovery-authority code must preserve
  this draft's immutable retirement barriers across restore, rollback and
  interrupted recovery. Shared auth/OIDC/settings code must preserve the approved
  audit-only administrator content policy and separate governed read grants.
- Shared installer/onboarding, chat/navigation, approvals and Copilot surfaces
  require semantic reconciliation. Whole-file replacement would discard fixes;
  source-specific tests must be repeated on the eventual combined tree.

No current implementation or local qualification here depends on adopting PR
#2531. Before either draft is combined with the other, its owners need to agree
on landing order, schema history and the approval undo/acknowledgement contract.
The same preservation rule applies to the separate PR #2529; PR #2530 remains
an audit source, not an imported implementation. No merge, deployment,
production migration or credential/configuration change is part of this review.
