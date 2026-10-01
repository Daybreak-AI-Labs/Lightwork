# Lightwork UX audit implementation

This register tracks the requested consolidation into draft PR #2528. It is an
implementation worklist, not acceptance or release evidence.

Start with the [review guide](review-guide.md) and
[195-entry checklist](implementation-checklist.md); the complete
JSON register retains the code evidence, validation and remaining criteria for
each item. [Local qualification](local-qualification.md) distinguishes failed,
focused and full-suite checkpoints.

- Audit/main baseline: `6daa3165ff5a844320ec970553d5ecdec7e396c4`.
- Preserved companion handoff: `d8904378537f0c04bd205a05446115687c2d0475`.
- Source: **Lightwork UX improvement backlog.csv**, 195 distinct audit entries.
- All 195 entries and all 24 source fields were retrieved from ten lossless
  Library parts, each checked for its expected entry count and end marker.
  `source-provenance.json` records the resolved source parts.
- Local tests and synthetic application journeys are now authorized, superseding
  the earlier test deferral. Live provider calls remain excluded. Historical
  slice validation notes describe their original checkpoints, not a prohibition
  on subsequent local verification.
- No merge, deployment, credential changes, or production changes are authorized.

## Work sequence

1. Preserve every source ID and its full acceptance criteria.
2. Address data loss, concurrent edits, permissions, and misleading results first.
3. Implement coherent personalization and product journeys within existing policy.
4. Record code evidence, remaining boundaries, and unexecuted qualification per ID.
5. Integrate independent commits into the existing draft branch and update its scope.

`implementation-tracker.json` retains exactly one row per audit ID. Statuses:
`planned`, `implemented-test-needed`, `already-fixed-test-needed`,
`test-needed` (qualification requires evidence not yet obtained), `blocked-source`, and `deferred-decision` (must name the actual decision and why
implementation depends on it). Partial code is not an implemented disposition.

Independent slices write their evidence to `slices/`; integration reconciles it
against the complete source backlog and shared contracts.
Run `python docs/ux-audit-2026-09-30/reconcile.py` after integration. Its explicit
ownership order prevents older category worklists from replacing newer focused
implementation evidence. No product or privacy decision is currently pending.
