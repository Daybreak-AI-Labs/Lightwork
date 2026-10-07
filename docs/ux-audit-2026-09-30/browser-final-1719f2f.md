# Final browser and JavaScript acceptance qualification

The complete local UI cohorts pass on immutable source `1719f2fc0e01216e8daea5f77228da9835409657`. The
accompanying JSON records every browser case, both native module case lists,
commands, source/lock/runtime hashes, and retained earlier failures. No test
retries or assertion-budget increases were used.

| Cohort | Result | Controller seconds |
| --- | --- | ---: |
| Full default browser collection | 101 passed, seven expected setup-profile skips | 785.797 |
| Setup profile | All seven skipped default cases passed | 46.797 |
| Distinct browser cases across both profiles | 108 passed, zero unresolved skips or failures | — |
| Focused original-criteria acceptance | 18 passed | 106.532 |
| Complete dashboard JavaScript | 1711 passed in 181 files; zero skipped, cancelled, todo or failed | 338.813 |
| Five complete native modules, ordinary | 69 passed, zero skipped | 36.0 |
| Same native modules, secure posture | 69 passed, zero skipped | 36.75 |

The native modules ran on `d4d6e25a402df02bec91abaa9d7126e60c3f895b`.
The only subsequent source changes were the workspace alert placement in
`lightwork-ui.js` and its browser regression. All Python product and native
test blobs are identical. Both secure flags were enabled for the secure
native run. One existing Starlette/AnyIO deprecation warning remains recorded.

## Original acceptance criteria now executed

- LW-091: empty-role keyboard submission makes no grant; creating and explicitly
  selecting a custom role records exactly that role. A deliberately delayed
  shared shell script reproduces early native form submission. Server-rendered
  tenant fields preserve the original workspace; missing/stale fields still
  receive 409 before any grant.
- LW-099: a real workspace switch in a second tab pauses an existing workflow
  draft and approval. Browser writes remain absent, direct stale writes receive 409,
  drafts and approval states remain intact, and the actual recovery link reopens
  the new workspace. The alert retains draft focus and fits 320px; the desktop
  link remains pointer-clickable. Fixed installations expose no ineffective
  selector. Signed-session 404 and missing-token 401 rendering are separately
  covered without granting access from scoped display cookies.
- LW-064/065: the actual embedded widget preserves input, Send, Close and uncertain
  receipt controls at 320px, landscape, 200% content zoom, RTL and an emulated
  keyboard viewport. Keyboard cases traverse pending, refused, unknown and
  accepted receipts without duplicate starts. The compact layout retains the
  original safe-area reserve. The retained landscape/zoom captures were visually
  reviewed as well as asserted through actual browser bounds.
- LW-066: reveal actions on five real page surfaces re-read reduced-motion preference and
  preserve focus. This is browser-emulated reduced motion, not a physical-device
  or assistive-technology certification.
- LW-102: real Keep selectors serialize explicit null for mixed/all retention
  resets; saved state remains correct after reload. The earlier 18-case native
  compliance-module evidence remains separately recorded.
- LW-130/137: exported task archives and selected-file bundles are received by
  independent offline viewers after the source installation is removed. Actual
  WebCrypto/parser paths validate provenance and refuse changed content,
  same-length tampering, missing/duplicate files, malformed/newer schemas and
  oversized input without retaining a previous task's content.

## Retained failures and repairs

The earlier complete baseline on `be3496571342c242af6ba956206e2446781996bb`
finished 95 passed, six failed and seven setup-only skips. Its setup run passed
all seven, yielding 102 distinct passes and six unresolved failures at that point.
Those results and their hashes remain in the JSON and task-local audit artifacts.

The role failure exposed a real fast native-submission race; all 11 Users POST
forms now receive a trusted server-rendered tenant binding before the shell
loads. Three widget failures exposed actual clipping, repaired with visual
viewport sizing and a compact short-screen layout. Workspace fixture corrections
use the real workspace route and an explicitly owned approval, honoring the
existing private-content policy. The wrong route also exposed a separate error
display issue; rendering now falls back to unscoped defaults when authentication
context cannot be resolved, while mutation authorization remains unchanged.

The next 18-case run on `d4d6e25` passed 17 and failed the actual workspace recovery
link click: its body-level alert sat beneath the fixed sidebar. The final repair
inserts it into the existing main-content layout. The desktop pointer assertion,
original draft/tenant safety assertions and original budgets remain intact.
The complete 18-case rerun and both final full browser profiles pass.

An earlier native attempt passed 65 and failed two new tests that incorrectly
expected 404 despite a configured bearer token with no supplied credential.
The tests now establish a real invited signed session for 404 and explicitly
preserve unauthenticated 401. The authorization contract was not relaxed.

Two pre-body Chrome launch attempts were stopped as infrastructure failures.
Native Windows Chrome requires normal Windows known-folder resolution; only
its USERPROFILE is inherited. A CDP launch probe verified its actual user-data
directory remained inside task TEMP. Node/product Python keep isolated homes;
no real Chrome profile, manual host registry/ACL changes, browser-security flag weakening,
live provider, deployment or production action was used. Windows Proactor
WinError10054 connection-reset callback warnings occurred in the earlier baseline
and final default run; final per-profile counts are preserved in the JSON. These
passing test results are not reported as warning-free.

## Exact source and environment

The qualification source and integration commit
`97ad6dea2a573f64dc1336e5fe84126fe824b920` have identical product and browser/JS
blobs. Their only differences are 19 audit documentation files and the separate
transport-supervision test diagnostics. Exact Git blob IDs are preserved in the
JSON. Every successful phase also verifies unchanged tracked source after exit.

Execution used local Windows Chrome 154.0.8037.58, Node 24.13.0, Playwright 1.58.2,
Python 3.12.12 and private dependency trees matching both committed lockfiles
(jsdom 29.1.1, parse5 8.0.1). Browser execution uses one worker, zero retries and
the original per-case/assertion deadlines. Synthetic local services and fresh
homes/data directories isolate all product state. Raw logs, JSON reports,
retained traces and process/source receipts remain under task-local `audit/`;
their hashes and case outcomes are committed in the accompanying evidence.
The browser harness clears inherited secure-default flags and applies explicit
synthetic identity/profile controls; this is not an additional forced-secure
browser run. Secure posture above refers specifically to the native module run.

Physical mobile keyboards, nonzero device safe-area insets, real OS text scaling
and audible screen-reader announcements remain device qualification limits.
The full Python suites and 3.10/3.11 interpreter floors are separate phases and
are not implied by these browser, DOM and focused-native results.

The passing final captures are byte-identical to those visually reviewed at the
focused checkpoint: [landscape](browser-evidence-1719f2f/widget-landscape.png) and
[200% content zoom](browser-evidence-1719f2f/widget-200-content-zoom.png).
