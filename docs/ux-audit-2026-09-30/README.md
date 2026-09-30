# Lightwork UX audit implementation

This register tracks the requested consolidation into draft PR #2528. It is an
implementation worklist, not acceptance or release evidence.

- Audit/main baseline: `6daa3165ff5a844320ec970553d5ecdec7e396c4`.
- Preserved companion handoff: `d8904378537f0c04bd205a05446115687c2d0475`.
- Source: **Lightwork UX improvement backlog.csv**, 195 distinct audit entries.
- The initial Library text extraction is incomplete after LW-157. Missing rows
  remain explicitly blocked on source delivery; no requirements are inferred.
- Regression tests may be authored, but test suites, application journeys and
  provider calls remain deferred at the user's instruction.
- No merge, deployment, credential changes, or production changes are authorized.

## Work sequence

1. Preserve every source ID and its full acceptance criteria.
2. Address data loss, concurrent edits, permissions, and misleading results first.
3. Implement coherent personalization and product journeys within existing policy.
4. Record code evidence, remaining boundaries, and unexecuted qualification per ID.
5. Integrate independent commits into the existing draft branch and update its scope.

`implementation-tracker.json` retains exactly one row per audit ID. Statuses:
`planned`, `implemented-test-needed`, `already-fixed-test-needed`,
`blocked-source`, and `deferred-decision` (must name the actual decision and why
implementation depends on it). Partial code is not an implemented disposition.

Independent slices write their evidence to `slices/`; integration reconciles it
against the complete source backlog and shared contracts.
