# Installed-browser and JavaScript checkpoint — 2026-10-01

All **78 distinct browser journeys** passed at immutable commit
`d4b71e3f11546568b234cd22535c0b507b7a2672`, using the complete default cohort and
its separate setup-enabled cohort. The complete dashboard JavaScript suite at
that same commit passed **1,686 tests across 177 files**, with no failures,
cancellations or skips. These results apply to this exact checkpoint; later
product changes require their own qualification.

| Run at `d4b71e3` | Passed | Failed | Skipped | Duration |
| --- | ---: | ---: | ---: | ---: |
| Complete default browser cohort, 78 collected | 71 | 0 | 7 setup-only | 1,094.486 seconds |
| All setup-only journeys, setup fixture enabled | 7 | 0 | 0 | 65.423 seconds |
| Complete Node/jsdom suite, 177 files | 1,686 | 0 | 0 | 408.667 seconds |

The seven default exclusions are the two connection-return, two setup-recovery
and three setup-readiness journeys. They all ran in the second profile; none
remain unexecuted. Deduplication by spec file and exact test title confirms 78
distinct successful journeys. Browser retries were zero, and both final reports
contain zero flaky results and zero top-level errors. A passing run does not
establish repeat-run stability.

## Exact environment and boundaries

Windows, installed Chrome `154.0.8037.58`, Playwright `1.58.2`, Python `3.12.12`,
Node `24.13.0`, jsdom `29.1.1`. Browser and JavaScript runs each used one worker.
The isolated checkout's interpreter imports all eight packages from that same
source, including subprocesses that remove `PYTHONPATH`. No dependencies or
host settings changed. The browser executable override is local and untracked.

Test servers bind loopback, use disposable stores, scrub credential-related
environment variables, and block outbound sockets before application imports.
Provider, model and identity responses are synthetic. Ordinary browser cases
block nonlocal requests. The catalog-cache case uses a rejecting loopback proxy
because Playwright request interception disables Chromium's HTTP cache; it
verifies that an external navigation is refused without contacting that host.
Windows guarded file recovery used the authorized local execution environment
that supports native directory handles; the production security checks remain.

The full log retains one Windows Proactor `WinError 10054` connection-lost
callback after peer socket closure. It occurred between successful recovery
journeys, with no application-route traceback or Playwright error. This warning
has not been silently removed from the evidence.

## Failures investigated and repaired

The earlier complete run at `68f2161` had 60 passes, three failures and seven
setup exclusions. Repairs aligned exact review labels, provisioned the fixture
tenant before application imports, and awaited actual navigation-shell readiness
before reading links. No owner or content-access guard was relaxed.

A later complete run at `bd4370b` had 62 passes, one failure and seven setup
exclusions. Every owner setup/login assertion finished before its 45-second
outer lifecycle expired during cleanup. The reviewed outer budget is now 90
seconds with named cleanup steps; individual assertions retain their original
10-second deadline. That journey passed in 52.4 seconds in the final cohort.

Cold shared-review discovery exposed an actual Action Center delay. The API
loaded all specialist deliverable specs before learning that the current owner
had no completed goals. `1c5c2f5` (integrated as `2c1e8a8`) defers that work until
the owner-scoped query returns rows. Nonempty projections still check current
suite access each time; no authorization-dependent cache was added. Empty,
nonempty, revoked, bounded-partial and unavailable behavior is covered by the
47 passing Action Center tests, including four new regressions. Ruff and
whitespace checks passed. The shared-review visibility assertion remains ten
seconds and passed in the complete browser cohort.

| Local request profile | Cold HTTP 200 | Warm HTTP 200 |
| --- | ---: | ---: |
| Before lazy projection | 4.208752 seconds | 0.107750 seconds |
| After lazy projection | 0.541107 seconds | 0.096342 seconds |

These are single cold/warm samples on the local test service, not a production
benchmark or latency guarantee. They support the profile showing unnecessary
specialist-pack parsing and imports; browser qualification is reported separately.

Catalog work also revealed a real `/preferences` HTTP 500: an interpolation value
named `source` collided with a translation helper argument. Root's `5edd114`
made source/language arguments positional-only and added native-page and escaped
interpolation regressions. The browser fixture now changes and acknowledges the
actual device-scoped interface preference rather than assuming `Accept-Language`
overrides it.

## New catalog coverage

Eight real-browser cases cover the public content-addressed catalog introduced
by `ef17c26`: all six supported language choices, SHA-256 URL/body agreement,
script initialization before dynamic controls, chat and preferences shells,
actual cross-shell HTTP-cache reuse (`transferSize == 0`), weak-ETag 304,
invalid-revision 404, no `Set-Cookie`, and an unavailable-script fallback.
Arabic copy without a shipped translation remains explicitly marked English;
this is not a claim of complete Arabic translation. All eight passed both the
targeted cohort and the complete final cohort.

## Retained evidence

The integration executor retains these immutable local artifacts outside the
application repository:

- `audit/browser-final-complete-d4b71e3-results.json` and `.log`.
- `audit/browser-final-setup-d4b71e3-results.json` and `.log`.
- `audit/browser-final-d4b71e3-summary.json`: exact counts, source, report hashes,
  environment and distinct-journey reconciliation.
- `audit/qualification-d4b71e3-all-js.json` and `.log`: exact counters and SHA-256
  manifest of the 177 versioned test files.
- `audit/action-center-cold-profile.json` and `action-center-lazy-profile.json`.
- Earlier `browser-final-*`, `browser-catalog-*` reports, screenshots and failure
  traces, plus JavaScript checkpoints `68f2161` and `4422b15`.

The final source also includes two sharing-lifecycle JavaScript regressions added
after the previous 1,684-test checkpoint. No file or test-name filter was used for
the complete 1,686-test run. This evidence does not certify the separate Python
suites, live IdP/provider behavior, paid model quality/cost reconciliation,
packaged desktop/native OS behavior, human translation review or customer
acceptance. No production change, merge or deployment occurred.
