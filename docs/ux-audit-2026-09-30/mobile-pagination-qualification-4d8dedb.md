# LW-052 mobile task pagination qualification

The repaired mobile list reaches and opens Task 125 in the 125-task fixture, keeps task IDs unique as new tasks arrive, removes deleted or inaccessible older rows on refresh, and distinguishes an empty search from an empty account. The complete local mobile suite passed **108 tests, with no failures, cancellations, or skips** at mobile source `4d8dedb11b940e98875867021b70f151e74c7fcb`.

The source, environment, commands, file hashes, and retained log hashes are recorded in the [machine-readable receipt](mobile-pagination-qualification-4d8dedb.json). These tests execute the real mobile TypeScript/TSX through the existing React renderer with synthetic APIs and native modules. They do not qualify an installed Android or iOS app.

## Reproduction and repair

At baseline `d17f7fa42065a04a9db4f6da150782a529a1e56c`, loading IDs 125 through 26 and then polling after insertion of ID 126 dropped ID 76. The next offset page could never recover it. A second regression showed that an older row removed from the authoritative response remained in the append-only cache. The initial three-case reproduction had **one pass and two failures**. Its failure log is retained; the initial test file was not separately committed before adding further cases.

The repaired list reads scoped, descending `before_id` pages from the newest task through the oldest previously visible ID. It publishes the complete refreshed window atomically. Loading older tasks extends that window by up to 50 rows below its previous boundary. Every request is limited to 50 rows and the whole fetch remains subject to the existing 20-second polling deadline. Refresh rechecks all visible rows against the server instead of keeping an unvalidated older-page cache.

The server dependency is backend commit `737ba45287df2c09370d5f83056889f2e5be16d0`. Its cursor is a strict upper ID bound under the existing access filters and does not look up a potentially deleted anchor. Mobile uses offset zero for cursor requests and validates descending, unique, positive safe-integer IDs. An older server that ignores the cursor produces a visible compatibility error; mobile does not fall back to offsets.

Settings, status, and search changes create separate generations. Cancellation, current-owner checks, and request sequencing reject superseded work and obsolete button callbacks. A 401, 403, or unsupported response from any page follows the existing polling denial path, clearing all loaded content and suppressing offline fallback. Stable ID keys and `maintainVisibleContentPosition` configure scroll preservation; native scrolling itself has not been exercised.

## Local validation

The complete suite includes 16 new pagination cases and one new API cursor wire-contract case. Coverage includes the 125-task journey, insertions and deletions between pages, removal of inaccessible older rows, no duplicates after refresh, later-page access loss, owner/status/search changes during pending requests, an ignored cursor, timeout recovery, duplicate load taps, superseded foreground refresh, unmount, and deterministic effect cleanup/setup replay.

Commands run from the repository root:

```text
node --test --test-concurrency=1 apps/mobile-companion/tests/*.test.cjs
node apps/mobile-companion/node_modules/typescript/bin/tsc --noEmit --project apps/mobile-companion/tsconfig.json
node --check apps/mobile-companion/tests/pagination.test.cjs
node --check apps/mobile-companion/tests/api.test.cjs
git diff --check
```

All final commands exited zero. The full Node suite took 27.266 seconds. Node 24.13.0, React and React test renderer 18.3.1, and TypeScript 5.3.3 were already installed in the existing mobile task workspace and were reused through owned directory junctions. No dependency installation or download was needed.

Intermediate failures remain in the receipt. One test initially expected a retry button for an unsupported build; the existing update-only UI was correct, so the expectation was corrected while preserving the content-clearing assertions. The first complete run had 41 passes and 67 invalid-hook failures because React and the renderer resolved different existing dependency trees. Adding the corresponding tests dependency junction resolved that harness setup error; product behavior and packages were not changed to bypass it.

## Qualification limits

Installed Android/iOS scroll anchoring, screen-reader focus, native networking, device keychain behavior, and human UX acceptance remain separate qualification work. Effect replay in the renderer is a deterministic ownership regression, not a claim of native StrictMode or device execution. Backend API and native PostgreSQL validation belong to the separate backend receipt. The full Python run frozen at an earlier source cannot qualify this later repair. No provider calls, deployment, merge, or credential/configuration changes were performed.
