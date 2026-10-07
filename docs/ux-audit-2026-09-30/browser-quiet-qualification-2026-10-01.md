# Quiet browser and JavaScript qualification — 2026-10-01

All **90 distinct installed-Chrome journeys** passed at immutable commit
`11c30996c98a0ad0c9583005223019ad0d75b8b4`. The complete JavaScript suite at the same source passed
**1,695 tests across 179 files**, with zero failures, cancellations or skips.
These results qualify this exact local checkpoint, not later source changes,
real provider behavior or the separate Python suites.

| Complete run | Passed | Failed | Skipped | Duration |
| --- | ---: | ---: | ---: | ---: |
| Default browser, 90 collected | 83 | 0 | 7 setup-only | 692.387 seconds |
| All setup-only browser journeys | 7 | 0 | 0 | 40.948 seconds |
| Complete Node/jsdom, 179 files | 1,695 | 0 | 0 | 335.815 seconds |

Exact spec-file/title reconciliation confirms 90 distinct successful journeys
and zero unexecuted cases across the browser profiles. Both browser reports
record zero flaky results and zero top-level errors. One worker, zero retries,
the original case order and existing assertion/test deadlines were retained.
The default case deadline remains 45 seconds and the assertion deadline 10
seconds; existing explicitly longer lifecycle cases retain their own budgets.

## Failure investigation and measured repair

The earlier complete concurrent run at `500fc87363a492def69722ef19d2cc518d171286`
passed 80 default cases, failed three, and excluded seven setup-only cases.
Those seven passed separately: 87 of 90 distinct journeys passed at that
checkpoint, so it was not a fully passing browser result. The two navigation
cases exhausted their outer deadlines, and outcome accounting did not finish
its visible read within the assertion deadline.

After the other heavy local jobs stopped, a quiet selected run at the same
source passed six of seven cases. The filename filter also selected the
fresh-owner navigation spec; all seven outcomes and traces were retained.
The remaining failure was the first navigation case: initial `/start` took
25.805 seconds, including 25.797 seconds waiting for the server. Later `/start`
responses were approximately 0.12 seconds. All five record searches returned
200 in 88–251 milliseconds, and the case exhausted its outer deadline during
the final destination's network-idle wait. Outcome accounting passed in 38.899
seconds. These runs differ in host load and fixture history; neither proves
that contention alone caused the earlier failures.

An instrumented loopback run exposed a smaller, concrete inefficiency: the
cold `/start` handler spent 2.082 of 2.515 seconds hydrating the entire bundled
specialist roster merely to decide whether a custom specialist existed.
The diagnostic Chrome case passed in 24.7 seconds. That profile did **not**
reproduce or fully explain the earlier 25.805-second server wait.

Source repair `e6ff3208da5b64ddd53d63464377f28e73e3ef32` (plus strengthened
regression assertions in `c97f9026c6f9bb8ce027706be6c67d936047dad1`) avoids
catalog probes when current owned task history already completes the setup
step. Empty or malformed-only active-workspace overrides also avoid loading
the bundled roster; parseable overrides still use the existing full resolver.
The same bounded no-follow parser and fresh workspace reads remain in use.
No permission cache, owner bypass or cross-request authority cache was added.

All 13 focused onboarding tests passed under ordinary and secure defaults.
Four new regressions verify catalog call counts, malformed input, workspace
changes/deletion, the existing resolver, and owner-scoped task progress. Ruff
and whitespace checks passed. A separate native profile measured cold `/start`
at 3.305 seconds before and 1.068 seconds after, with the same 154,146-byte
response; warm samples were 0.205 and 0.266 seconds. These are individual local
samples, not a production benchmark or a latency guarantee.

The complete affected six-case cohort then passed at `c97f902` in 105.331
seconds. In the final complete run at `11c3099`, both navigation cases
passed in 20.2 and 20.1 seconds and outcome accounting passed in 38.2 seconds.
No additional timing adjustment or automatic retry was used. A passing
checkpoint does not establish repeat-run stability or explain every earlier
timing outlier.

## Environment and scope

Windows; installed Chrome `154.0.8037.58`; Playwright `1.58.2`; Python `3.12.12`;
Node `24.13.0`; jsdom `29.1.1`. Each job used one worker and ran sequentially
while other heavy qualification jobs were held. The clone's interpreter imports
its matching source, including subprocesses that clear `PYTHONPATH`. The tracked
checkout was clean and its HEAD unchanged before and after qualification.
The installed-Chrome executable override is the only local untracked config.
No dependency, host, credential or deployed-provider configuration changed.

### Fixture posture, not a production-posture certification

The hashed launch harness strips every inherited `MAVERICK_*` variable,
including `MAVERICK_SECURE_DEFAULT` and `MAVERICK_TEST_SECURE_DEFAULT`.
`server.environment()` repeats that scrub and does not restore either flag.
Its config has no `security.secure_defaults` override. These standalone browser
servers do not import pytest's root conftest, which otherwise pins the ordinary
Python-suite default to zero. Source inspection therefore resolves the core
`security_defaults.secure_by_default()` fallback to true, consistent with the
retained audit-signing log. This is an exact-source assessment, not a captured
live process-environment receipt or evidence that every deployment knob uses
its production default.

The main fixture explicitly sets `MAVERICK_PROFILE=standard`,
`MAVERICK_ENTERPRISE=0`, `MAVERICK_ENCRYPT_AT_REST=1`, disposable encryption and
publication keys, selected feature flags and synthetic authenticated principals.
The enterprise fixture reuses this environment and adds real local invitation,
cookie and role flows with synthetic connection exchanges. The owner fixture
independently scrubs both flags, explicitly enables encryption, and uses real
local owner setup/login without an injected principal. None exercises a live
IdP or all enterprise/compliance deployment floors.

Simply rerunning this existing harness with an inherited secure-default flag
would not produce a distinct secure-mode run: that flag is removed before the
application starts. A future dedicated posture variant must explicitly define
and assert its effective fixture controls. No such additional run is claimed.

Browser fixtures bind loopback, use disposable encrypted stores, scrub
credential-related environment variables, and block outbound sockets before
application imports. Provider/model/identity exchanges are synthetic. Existing
native Windows file guards remain enabled. The catalog cache journey uses its
rejecting loopback proxy because request interception disables Chromium caching.

The full default log retains one Windows Proactor `WinError 10054` socket-close
callback during a passing workspace recovery case. Its stack contains the
asyncio socket shutdown callback, with no application-route traceback or
Playwright error. It was not removed from the qualification evidence.

Real IdP/provider consent, refresh/revocation, model answer quality, actual paid
cost settlement, packaged desktop/native OS behavior, human accessibility and
translation review, and customer acceptance remain separate qualification.
No production change, merge or deployment occurred.

## Retained evidence

The executor retains complete logs, reports, screenshots and traces outside the
repository. The following JSON report hashes bind the final browser counts:

| Report | SHA-256 |
| --- | --- |
| `audit/browser-quiet-complete-11c3099-results.json` | `5329f479b1cee32e6e48d6a72ed9a8e118a822c17fd727a5d854679818c4bceb` |
| `audit/browser-quiet-setup-11c3099-results.json` | `d9a0383fd684291017744f350edad083720c0f0b2de6abe1c39927eaea979c6f` |

- `audit/browser-final-11c3099-summary.json`: exact source, environment,
  report/log hashes and 90-title reconciliation.
- `audit/browser-quiet-11c3099-source-manifest.json`: runner, local config and
  41 versioned fixture-file hashes, clean tracked-source check and deadlines.
- `audit/qualification-11c3099-all-js.json` and `.log`: exact counters and the
  SHA-256 manifest of all 179 versioned JavaScript test files.
- `audit/qualification-11c3099-final-evidence.json`: parsed JavaScript counters,
  hashes of the JavaScript log/manifest and browser summary/source manifest,
  and the before/after qualification source-check receipt.
- `audit/browser-quiet-specs-500fc87-results.json` and `-network.json`: all seven
  earlier quiet outcomes and timing evidence; corresponding logs/traces retained.
- `audit/browser-profile-navigation-500fc87-results.json` and `-network.json`,
  `browser-loopback-start-profile-500fc87.json`,
  `browser-cold-start-profile-500fc87.json` and
  `browser-cold-start-profile-fixed.json`: diagnostic profiles and comparisons.
- `audit/browser-quiet-repaired-specs-c97f902-results.json` and `-network.json`:
  the complete affected six-case cohort after repair.
- `audit/browser-start-spy-ordinary.xml` and `browser-start-spy-secure.xml`,
  with their logs: 13 focused native passes in each posture.

The earlier `d4b71e3` full checkpoint and later `500fc87` concurrent failures
remain documented independently; no historical result was overwritten.
