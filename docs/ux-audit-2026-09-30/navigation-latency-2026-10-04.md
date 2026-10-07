# Navigation loading correction — 2026-10-04

This scoped follow-up to draft PR #2529 starts from
`dc601518d50161f3dc6d411e576743ba0a9513f5`. It addresses a reproduced browser
navigation stall and unnecessary work on the getting-started page. It does not
qualify the entire consolidated audit or establish production response times.

## Reproduction and change

On the starting revision, holding the localization catalog response prevents the
browser from parsing the page body: `/chat` has no `main` element after 1,500 ms.
The catalog and runtime were synchronous scripts in the document head.

The three page shells now preload localization resources in the head and execute
them in order after the primary markup. Shared navigation, mobile drawers,
workspace submission protection, and emergency-stop status verification initialize
before that download finishes. Emergency controls retain their existing authority
and current-state checks; late translation uses the latest verified status. The
temporary English copy declares its language. Initial toast announcements wait
for the language helper or its fallback. Workflow and file-recovery controllers
still execute after localization, and recovery-mode pages retain the runtime.

The getting-started page reuses its provider inventory within one request for both
preflight reports. Both preflight checks still run; a subsequent request reads a
new inventory and responds to readiness changes. There is no cross-request cache.

Both main shells already advertised the notification badge script, but its route
was missing. Explicit asset registration restores the JavaScript response and
removes its repeated 404/MIME errors. Four existing workflow-disabled Chat strings
also received the missing French, German, Japanese, and Chinese draft catalog
entries. Language and consequence-copy qualification remains open.

## Focused local verification

Checks used an isolated Windows checkout, synthetic local state, and local Chrome.
Test processes received no live provider credentials; the browser fixture blocked
outbound requests. No paid model calls or production mutations were made.

| Check | Result |
| --- | --- |
| Python: ten affected dashboard test modules | 187 passed |
| Same Python cohort with secure-default flags enabled | 187 passed |
| JavaScript: seven affected shell/navigation test files | 64 passed |
| Chromium: localization, customer navigation, and file recovery | 23 passed, no skips or retries |
| Repository Ruff and Vulture | Passed |
| Dashboard wheel build and source payload comparison | Passed; 12 changed product payloads matched |
| Whitespace review | Passed |

Each Python run reported one existing Starlette/AnyIO deprecation warning. The
ordinary Python, JavaScript, final browser, and static/build runs recorded unchanged
source hashes during execution. During the secure-default run, only a template
comment changed; executable source and assertions did not change.

The Python cohort consists of `test_onboarding_probe_scope.py`,
`test_guided_outcome.py`, `test_journey_catalog_assets.py`, `test_journey_i18n.py`,
`test_shell_i18n.py`, `test_customer_navigation_permissions.py`,
`test_workflow_builder.py`, `test_task_file_recovery_api.py`,
`test_personal_notification_badge_journey.py`, and `test_advertised_assets.py`.
Both postures used `python -m pytest` with isolated temporary state. The secure
posture set `MAVERICK_TEST_SECURE_DEFAULT=1` and `MAVERICK_SECURE_DEFAULT=1`.

The JavaScript cohort consists of `shell_i18n.test.js`,
`lightwork_ui_halt.test.js`, `lightwork_ui_navigation.test.js`,
`lightwork_ui_forms.test.js`, `chat_shell_drawer.test.js`,
`journey_catalog_asset.test.js`, and `lightwork_ui_plain_language.test.js`, using
Node's test runner serially. Browser coverage ran `locale_catalog.spec.js`,
`customer_navigation.spec.js`, and `task_file_recovery.spec.js`; the separately
named fresh-owner journey was excluded from this final scoped cohort.

The final browser run deliberately withheld each localization response until
primary content and navigation were visible. Mobile cases also opened the drawer
while the response remained held. These measurements start when the held asset
request is observed; they are local regression evidence, not end-to-end latency
benchmarks or a production service-level promise.

| Page | Held response | Viewport width | Content/navigation visible |
| --- | --- | ---: | ---: |
| Chat | Catalog | 1280 px | 88 ms |
| Chat | Catalog | 390 px | 517 ms |
| Chat | Runtime | 1280 px | 74 ms |
| Chat | Runtime | 390 px | 529 ms |
| Preferences | Catalog | 1280 px | 114 ms |
| Preferences | Catalog | 390 px | 289 ms |
| Preferences | Runtime | 1280 px | 91 ms |
| Preferences | Runtime | 390 px | 590 ms |

The same tests checked current emergency-stop availability while localization was
held, then French dynamic controls after release. Existing navigation checks at
100% and 200% content zoom, restricted-user direct-route checks, catalog cache
reuse, workflow example interaction, and file-recovery conflict handling passed.

The locally built `maverick_dashboard-0.1.7-py3-none-any.whl` has SHA-256
`c21cc0d55dd62b21bf5b40e6269f9c9e920be0052829159e90751e9872087a3e`.
The wheel was inspected, not deployed.

## Scope and remaining qualification

The full monorepo and installed-desktop suites were not rerun for this scoped fix.
Hosted CI was not requested. Existing PR qualification limits remain in force.
An unauthenticated read of the known public host returned a healthy liveness
endpoint and an authentication challenge at the root; its running commit and
signed-in navigation performance were not established. Cold local profiling also
included startup warming, so it does not establish a separate production cause.
This change has not been merged or deployed, and no deployment credentials or
security settings were changed.
