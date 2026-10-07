# Late theme, drawer and route qualification

Final acceptance review reproduced two product defects after the `2c1e8a8`
runtime checkpoint. A valid four-colour white custom theme omitted semantic
colours and inherited unsuitable dark-theme defaults. Resizing an open mobile
drawer could leave focus on its hidden Close control, or on the document body
after Chromium blurred that control before its media-query callback.

`5bc069b` derives omitted semantic colours from the validated text colour in both
the server palette and isolated preview. It also adds the initial resize repair.
`3f8dc75` completes the native browser case by retaining the drawer's last actual
focus target. Visible navigation and an open Find dialog retain their focus;
only the disappearing Close control or its browser-generated loss of focus
transfers to a visible drawer target. No permission or content policy changed.

- Theme Python cohort: 24 passed in ordinary mode and 24 with both secure-default
  flags. The three complete files cover theme contrast, density and dark-mode
  accessibility. Task-local evidence: `audit/late-theme-{ordinary,secure}-01.*`.
- Focused theme and drawer Node/jsdom cohort: nine cases passed, including the
  blur-to-body event ordering.
- Four new installed-Chrome journeys passed in 26.3 seconds, without retries.
  They exercise the actual account/device display picker, computed semantic
  colours and borders, Close-control resize, navigation-focus preservation and
  the Find-dialog handoff. The tested tree is
  `205d2753877341f8635cdc92443fce55c81a3d88`, committed by the worker as
  `8db2fc1d090b403c8c7a0d1f1616f687436bf219` and integrated as `3f8dc75`.
  Local report: `audit/browser-theme-drawer-fixed-results.json`; the white-theme
  preview screenshot was also inspected. Human assistive-technology and native
  device qualification remain separate.

The frozen full ordinary run then identified 17 assertion failures in two
dashboard test modules. `a726d51` corrects their expectations without weakening
their application contracts. Protected note dispatch asserts all six arguments,
including the exact owner and execution identity. Governance asset checks use
Starlette's actual full GET-route matching for content-addressed parameterized
catalog URLs, followed by real HTTP 200 and MIME assertions for every asset.
The two complete modules passed **46 cases with two POSIX-only skips in each
posture**. Both secure switches were explicit. The tests ran against `f3a18b8`
with exactly the two-file patch subsequently committed as `a726d51`.
Logs and JUnit: `audit/late-route-dispatch-{ordinary,secure}-01.*`.
Ruff and whitespace checks passed for the changed Python tests.

These focused results do not turn a failed or incomplete full run into a pass.
The consolidated source still requires fresh complete runtime and browser runs.

The next frozen run at `500fc87` exposed one further outdated Settings assertion
in both postures: it expected omitted semantic colours to remain absent after a
save. `00c1b84` instead asserts every exact completed colour, including the
explicit warning override. It retains theme selection and invalid-input denial
checks. The complete `test_settings_catalog_access.py` module then passed
**417 cases with two POSIX-only skips in each posture** (402.90 and 394.06
seconds), against unchanged product source plus that test-only correction.
Evidence: `audit/late-theme-catalog-{ordinary,secure}-01.*`. Ruff and whitespace
checks passed. The original full-run failures remain recorded separately.

The next full-run helper disables only the optional timed traceback dump that
was active during the Windows worker crash. Fatal traceback reporting remains
enabled, as do the existing 180-second test timeout and the self-harness's own
900-second cap. The setting is recorded in each new source receipt and can be
overridden explicitly. Both unchanged isolated 1,000-round self-harness runs
passed with and without that diagnostic; three bounded pure-CPython probes did
not reproduce the crash. Its cause remains unproven, as recorded in
`windows-timed-dump-investigation.md`. This diagnostic change is not a repair
claim and does not change application ACL checks, assertions or test selection.
