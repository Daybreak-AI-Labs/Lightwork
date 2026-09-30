# Catalog fixture and Windows qualification checkpoint

Existing catalog mutation fixtures now obtain the authenticated all-layer revision before submitting a save. Explicit stale revisions are preserved. Reset assertions distinguish an intentional product-default mask from inherited absence. Missing samples for immutable workbench images and approved account profiles are supplied; plain-language descriptions avoid developer terminology.

Windows qualification no longer fails every integration case because an autouse fixture attempts privileged symlink creation. Only the two actual link-replacement scenarios skip when Windows reports privilege 1314. POSIX permission-bit checks remain explicitly skipped on Windows. Program validation now rejects Windows documents selected as executables; the runtime chooser fixture uses a Windows executable suffix and preserves its document-refusal assertion.

Validation performed locally, no provider/network calls:

- Initial repaired five-module sweep: 1,381 passed, two Windows privilege-dependent scenarios skipped; eight failures identified (two stale HTTP mocks and six product-default mask expectations). Those causes were repaired.
- Follow-up selected six reset cases: six passed, 670 deselected.
- Follow-up Gist/Pushover mocked HTTP assertions passed before the later run encountered the missing test import; that test import is corrected.
- Structured module: 27 passed, two POSIX scenarios skipped; the Windows document-as-program failure was fixed and its targeted regression passed.
- Earlier access sweep: 400 passed; all eight reported causes were repaired and the targeted rerun passed seven with two platform qualifications skipped.
- Earlier remaining-category sweep: 335 passed before the old integration fixture errors; no complete all-catalog pass is claimed. Root owns the integrated full-suite rerun.

Ruff and whitespace checks apply to the changed fixture/product files. No skips conceal a failed supported platform behavior.


## Follow-up qualification on integrated source 7b91956

The editor/settings JavaScript run first reproduced six failures. Checkpoint 65bf8ef fixes actual draft-recovery gaps and refreshes fixtures to the current contracts: workflow field drafts retain incomplete names and raw whitespace; fact saves preserve entered/newer text until a native record readback confirms exact content and a newer revision. Revoked access still clears private content. All 34 focused JavaScript tests passed; Jinja/JavaScript syntax and whitespace checks passed.

Seven remaining Python families (`runtime_wiring`, `guided_forms`, `all_page`, `admin_policy`, `runtime_safety`, `catalog_finance_profile`, `voice_unlock`) initially produced 129 failures, 5 errors and 67 passes. Six fixture modules omitted the required reviewed configuration revision. Updating them to the shared authenticated revision helper exposed 197 passes and 4 remaining failures. Their assertions still verify effective reader values, confirmation/quorum, refusal, audit behavior and source provenance; raw malformed JSON tests now include a valid revision so they reach their intended validation boundary.

The remaining real Windows issue was child-log CRLF framing; the supervisor now removes the line-terminating carriage return without trimming meaningful spaces. A dedicated split-chunk regression and the real local child-process lifecycle test pass. The reset-label assertion now distinguishes product default from inheritance.

Configuration/foundation qualification initially produced 1,596 passes and 5 failures. The missing `device_pairing` section is registered as known configuration. The two lock tests now probe from a competing thread, because the settings lock deliberately supports reentrant native reviewed operations; they still require exclusion at both strict read and write boundaries. The complete foundation suite plus the Windows/wording regressions then passed 1,604 tests.

Protected global-settings approvals had also become unreadable to other global administrators under the employee-content privacy gate. The narrowly scoped exception requires a current global administrator, no task content, the exact native action/provenance/scope, and an authoritative settings proposal binding the approval ID, requester, quorum, target/parameter commitments and exact card hash. Labels or a self-consistent copied card alone do not grant access. New requests persist that binding; older unbound proposals require a fresh explicit settings review before application. The focused native quorum tests verify distinct-admin voting, exact secret binding, and refusal of copied IDs/task cards/tampered content. Existing requester separation-of-duties and private employee content rules remain enforced.

The expanded safety/voice/privacy run passed 45 tests and exposed three obsolete privacy fixtures lacking request.state. After adding the real request-state shape, all 13 privacy cases passed. All 35 safety/voice cases passed, including the new legacy-proposal refusal test. The final form-controller follow-up passed 17 tests, including revocation during list refresh. The broader seven-module catalogue round-trip run is still in progress at this checkpoint; no complete-pass claim is made for that run. No live provider calls, production writes or deployment were used.

## Reviewed administrative contracts on integrated source 4d0a16f

The eight additional consent, compliance, billing, audit, model-picker and UI-review families initially produced 19 failures and 57 passes. Their updated native contracts now pass all 76 tests. Consent settings obtain a fresh catalog revision. Compliance export explicitly verifies the default privacy denial and self-review denial, then creates and independently approves a scoped named-account grant before export. Retention uses the retained review, confirms holds and checks that execution uses the same frozen cutoff as preview. Stripe uses the real reviewed stage service with a fake provider, checks durable intent before dispatch, refuses changed reviewed fields and verifies that repeated confirmation does not dispatch twice. No provider service or credential is contacted.

The audit-folder fixture now uses the host path separator, retaining all outside-root and overlap refusals on Windows. Model-picker assertions inspect actual select/option structure rather than attribute order. Audit filtering verifies that event metadata remains visible while private agent labels remain absent. The product feature switches for personal preferences and configuration profiles now include their missing explanatory descriptions; the exhaustive description-key assertion remains intact.

The settings-file installation test now supplies the exact-file review and verifies stale-review refusal, backup bytes, intent-before-confirmation audit order, acknowledged audit status, replay without another backup, and a fresh unchanged-file review. That targeted case passes. The existing billing-review and retention-receipt safety modules also pass all 11 cases. Ruff and whitespace checks pass for the changed files. The separate broader catalog round-trip run remains in progress and has exposed remaining failures; this checkpoint does not claim that matrix is complete.
