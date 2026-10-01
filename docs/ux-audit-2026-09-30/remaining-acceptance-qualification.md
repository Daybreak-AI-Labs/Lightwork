# Original-criteria acceptance checks

Execution follow-up: all seven browser cases authored here now pass within the
complete 108-case qualification, alongside 11 widget/motion cases. See
[browser-final-1719f2f.md](browser-final-1719f2f.md) for the current result,
subsequent product repairs and retained failures. The account below records the
original authored checkpoint.

This checkpoint adds targeted acceptance checks for LW-091, LW-099, LW-102,
LW-130 and LW-137 on product source
`7798b3db81c5ec4f718eeeeefefdafc6cb7cf8a7`. It changes no product behavior.
The original source backlog is unchanged. The full consolidated source and
browser run remain owned by the integration task.

## Executed retention regression

The entire `test_compliance_controls_api.py` module now has 18 cases. Three new
cases exercise the actual save/readback route: clear one 30-day period while
saving another and preserving omitted fields; clear all periods then reload
configuration and call native retention enforcement against old synthetic
records; explicitly refuse a forced effective policy override. Change auditing
remains asserted. Only the external policy layer in the last case is mocked.

Native Python 3.10.19 executes the isolated clone's first-party source with the
previously verified private compatibility environment's third-party libraries.
The environment retains only OS/tool-discovery variables, uses a fresh home and
temporary directory, disables model downloads, and loads the local external
network guard. This focused source run is distinct from the frozen wheel-based
full compatibility qualification.

Both ordinary and secure postures passed all 18 cases in the first attempts.
After two lint-only comprehension changes, attempt 02 again passed all 18
cases in both postures (ordinary 22.72 seconds, secure 22.23 seconds). Exact
case IDs and artifact hashes are in the accompanying JSON receipt. One existing Starlette/AnyIO deprecation warning is retained; there
are no skipped cases in this module. No live provider, deployment or production
operation occurred.

## Browser acceptance at the original authored checkpoint

At this checkpoint Chromium execution was held until the concurrent full Python
phase finished. The following then-described authored tests were not yet passing
browser evidence:

- `archive_receiving.spec.js` has three cases using native goal-archive and
  selected-deliverable exports. The source installation is removed, then a
  separate offline browser opens the exported HTML, checks the actual WebCrypto
  checksum, schema/refusal behavior, selected content and unresolved items.
  Tampered, missing and duplicate selected files must fail verification. No
  execution authority is imported and no network request is permitted.
- `workspace_switch_acceptance.spec.js` has two cases using a real invited
  account and two native workspace stores. One tab changes workspace while
  another retains a workflow draft and another has an undecided approval. The
  tests require a visible notice, zero stale browser mutations, server rejection
  of a stale expected-tenant header, unchanged approval states and safe reopen.
  A separate fixed-binding case requires no ineffective selection controls.
- `retention_acceptance.spec.js` drives the actual Keep selectors, mixed/all
  saves, explicit null serialization and reloaded effective state through the
  native endpoint.
- `role_grant_acceptance.spec.js` uses keyboard submission on the actual Users
  form. An empty initial role must prevent submission; creating and explicitly
  selecting a custom role must record exactly that grant through the native API.

The fixture uses real single-use invitations, signed cookies, native RBAC and
workspace stores. Test-only fixture endpoints are not product routes. The
standalone archive generator preflight completed successfully; that does not
qualify a browser's rendering or cryptography. Python 3.10 AST, focused Ruff,
JavaScript syntax and whitespace checks passed.

## Correction to earlier archive evidence

The 12 passing `test_offline_bundle.py` cases exercise the separate
`maverick-offline/1` mobile snapshot. They do **not** execute the v1 task archive
viewer, its malformed/newer-file refusal, or WebCrypto. Those earlier test
counts are valid, but the LW-137 coverage attribution was inaccurate. The
five native `test_goal_bundle_completeness.py` cases prove producer-side
snapshot/count/checksum bounds. The register and conversation evidence now make
that distinction. Offline receiving was unqualified at that authored checkpoint;
the linked final report now records execution of the actual offline browser cases.

Collection-only Playwright check lists all seven cases in four files; Chromium
was not launched. An initial collection hit Windows system-temp permissions;
a second resolved an ancestor host Playwright instead of the pinned runner.
The successful third attempt uses task-local TEMP and a private copy of the
already available pinned dependencies. Both failed attempts are preserved. An
independent review strengthened per-iteration digest completion and guarded
workflow-save refusal assertions before this checkpoint.
