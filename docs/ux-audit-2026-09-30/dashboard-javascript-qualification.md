# Dashboard JavaScript qualification - 2026-09-30

The later [2026-10-01 checkpoint](browser-and-javascript-checkpoint-2026-10-01.md)
records the complete 177-file suite at `d4b71e3`: **1,686 tests passed**, zero
failures, skips or cancellations, concurrency one, 408.667 seconds. The historical
1,680-test run below is retained with its original source and repair provenance.

**1,680 tests passed across all 176 dashboard `*.test.js` files**, with zero failures, skips or cancellations, at the immutable source commit `f036cef90a6d6fd1ddaa3f76bd857b9ca9bd4c3e`. The complete run finished in 116.9 seconds and returned exit code 0.

This was the whole dashboard Node/jsdom set at that commit, not only changed controllers. It includes conversation, uploads, result replacement, public-link management, read-aloud, account/context changes, reviewed operations, governance, editor recovery, localization, workflow account selection and the six new saved-task-view cases. It is synthetic local qualification; it does not certify installed browsers, real external providers or the separate full Python run.

## Exact execution environment

- Fresh isolated clone `slice-javascript-final`; no user working tree or production state used.
- Node `v24.13.0`, shared existing jsdom `29.1.1`; no JavaScript dependency changes.
- `run-all-js.py` invokes `node --test --test-concurrency=4` with the sorted explicit list of all dashboard `*.test.js` files, using `packages/maverick-dashboard/tests/js` as the working directory. No forced exit, omitted file or filtered test name was used for the complete run.
- Python subprocesses use the audit Python 3.12 interpreter and all eight package source directories from this same checkout, including the installer source. They do not import the editable root checkout accidentally.
- No live provider/model/credential calls, browser deployment, merge or production action was performed.

## Failure and repair provenance

The first complete run was pinned to `a0c9efd205b0319f81aca41ff5742ed8f0df1c40`: 175 files, 1,674 tests, 1,673 passed and one failed, with no skips or cancellations. Its sole failure was `audit_file_selection.test.js:90`, whose miniature DOM omitted the new `connection_references` field used by prepared template admission.

The localization worker repaired that fixture in `0ab4c85c49a228a77b4f8405b3bb091eac1a309e` (root integration `44d67e8`). The test now includes the real hidden field and verifies exact preservation of its selected account references. It continues to assert translated notice, exact source text, no HTML execution and no automatic submission. No product behavior or access guard was weakened to obtain a pass.

The second run advanced the clean clone to `f036cef90a6d6fd1ddaa3f76bd857b9ca9bd4c3e`, including that repair, saved views from root `cb21d41`, the settings worker's `editor_recovery` helper alignment, workspace feedback isolation and shared hero escaping. All 176 files then passed. The settings fixture change supplied the real `productText` helper while retaining recovery assertions; it was not a new failure in the first pinned run.

During the final review, the saved-view adapter existed but was absent from the explicit catalog registry. Root fixed both the lazy import and tuple entry in `b89320c00a5be417b05ae86c93385b6c27438133`. The diff from the tested `f036cef` source to that follow-up is exactly two lines in `product_operations.py`, with no JavaScript or template change. The operation matrix now traces `task_views.status/save/remove` to the actual native service. This source check is not reported as an additional Python test pass.

## Retained evidence and limits

The integration executor retains:

- `audit/qualification-a0c9efd-js.log` and `.json`: first complete result plus the SHA-256 manifest of its 175 test files.
- `audit/qualification-f036cef-js.log` and `.json`: passing complete result plus the SHA-256 manifest of all 176 test files.
- `audit/slice-javascript-final-all-js.log`: the final full console output.

These runtime artifacts are outside the repository and contain local synthetic results. The 14 environment skips recorded in the separate conversation Python qualification are not JavaScript skips; this complete JavaScript run has none. The separate consolidated Python run was still being qualified after a worker failure and is not certified by this document. Installed keyboard/screen-reader/browser and deployment-specific integrations remain governed by their own evidence.
