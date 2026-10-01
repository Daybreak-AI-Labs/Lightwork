# Coordination with the separate teardown draft

This is a read-only comparison of PR #2528 at
`11438635213c90350aca4d8e9090c47ffb4756b7` and the user's separate
[PR #2531](https://github.com/Daybreak-AI-Labs/Lightwork/pull/2531) at
`d24de994c5f1feab822b998f2571136ebaadbd9d`, against their common
`6daa3165ff5a844320ec970553d5ecdec7e396c4` baseline. No code from that PR was
imported, executed, merged or changed. Its author-reported tests are not evidence
for this draft. The [exact inventory](concurrent-pr2531-overlap.json) records
1,334 changed paths here, 2,134 there, and **88 shared paths**. Shared paths are
not automatically textual conflicts.

GitHub metadata confirmed the separate PR is still open, draft, and unmerged at
the pinned head. The previous comparison heads and counts remain in the JSON
inventory so this update does not erase its earlier scope.

## Latest eight-file advance

The [exact two-commit advance](https://github.com/Daybreak-AI-Labs/Lightwork/compare/e2014c94d71a3e21f226e6fc461351ae9a022008...d24de994c5f1feab822b998f2571136ebaadbd9d)
changes eight files, with 55 insertions and 20 deletions. The JSON inventory binds
all old/new file blobs and the read-only patch hash. None of these changes was
imported or executed.

| File under `packages/maverick-dashboard/` | Reviewed change | Also changed on our branch since the common baseline? |
| --- | --- | --- |
| `maverick_dashboard/static/lightwork-ui.js` | Guard timestamp/toast DOM queries in minimal scripted harnesses; start the relative-time timer only if timestamp elements exist | Yes |
| `maverick_dashboard/templates/api_keys.html` | Escape the literal hyphen in the service-account name input pattern | No; template absent here |
| `maverick_dashboard/templates/privacy_rights_form.html` | Escape the literal hyphen in the property slug input pattern | No; template absent here |
| `tests/browser/customer_navigation.spec.js` | Distinguish Most used links from grouped navigation links | No |
| `tests/browser/fresh_customer_navigation.spec.js` | Cover their owner-first redirect, Skip for now, and confirmation passphrase | Yes |
| `tests/browser/owner_form.spec.js` | Adapt to their owner-first redirect and test mismatched/matching passphrases | Yes |
| `tests/js/goals_search.test.js` | Adjust expected links for their unified task navigation | No |
| `tests/js/ops_pages.test.js` | Use their combined tenant/invitation form and advanced disclosure; exclude a second creation request | Yes |

These are **four overlapping paths within the latest delta**, not four newly
established textual conflicts. The timestamp formatter block changed by the
other PR is absent from our pinned `lightwork-ui.js`; whole-file replacement
would discard our separate workspace-binding and other shell behavior.

There is no direct source-backed fix for our current role-grant browser failure
in this delta. That test observed a role still `null` after explicit keyboard
submission. Their changed HTML patterns belong to the service-account and
public-rights forms. Our `users.html` role grant uses a required select, and its
custom-role name input has no `pattern` attribute. Neither `users.html`, the
`/users/set` handler/RBAC implementation, nor our `role_grant_acceptance.spec.js`
is changed in this eight-file advance. Our shell's workspace-binding submit
listener is also outside their timestamp/toast edits. The role-grant failure
therefore remains with its own investigation; this comparison does not identify
its cause or establish that the other PR repairs it.

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
| 46 | Exact goal connection selection | - |

The latest eight-file advance leaves PR #2531's migration lock byte-for-byte
unchanged. At the compared heads, our version-42 fingerprint is
`2c22dea0a5f80cca` on both backends; theirs is `a8fe9668749d588a` for SQLite and
`9a14697694262df8` for PostgreSQL. The collision is still present.

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
