# Dashboard fixture qualification, 2026-10-01

This follow-up qualifies the dashboard repairs on product source
`a9211f8178cbe016979e30eff8452085b841b695`, with fixture checkpoint
`8c01f4b1347b215505730d0838733e7066e1f52c` and the test changes in this
commit. It does not modify the frozen full-suite checkout or assert that the
entire repository passed.

The original exact-markup checks failed when localized text gained language
attributes and nested spans. The replacements inspect actual rendered elements,
requiring exact option values, labels, link destinations, and navigation
landmarks. The base-layout landmark test visits `/overview` explicitly because
`/` selects the chat or setup shell according to model readiness. Error-page
checks inspect visible content rather than inert translation JSON, retain the
exact API error body, and assert the corresponding HTTP denial statuses.

The Cortex fixture now uses the production `_theme_context` processor. Its
permission, audit visibility, and CSP assertions remain intact. The oversight
fixture requires exactly one five-column denial event, excludes the approved
action, and keeps the prohibition on raw action details.

The audit signing fixture now resets the documented test-only injected-key
cache before and after each case. Production intentionally consumes and caches
its startup key; clearing environment variables alone cannot change that key.
Both injected and local status cases use the real signer and verify the actual
key or injected-source marker on disk. No key-custody behavior was changed.

The sharing controller already distinguishes a server failure from revoked
access. The source contract now retains `button.disabled = denied`, and two
new browser-DOM regressions prove that an explicit retry is available after
503, while 403 clears private metadata, disables controls, and prevents further
requests. No automatic mutation retry was introduced.

## Execution and provenance

Windows Python 3.12 used the existing task-local `.venv-audit`; `PYTHONPATH`
selected all eight package source directories from `slice-workflow-qualification`.
Every run used an isolated task-local `--basetemp`, `python -m pytest`, and
`--timeout=180 --timeout-method=thread`. No dependencies changed and no live
provider calls were made.

The nine-module follow-up cohort consists of `test_cortex_page`,
`test_deliverables`, `test_oversight_console`, `test_plain_language_catalogs`,
`test_ui_nav_and_form_errors`, `test_qa_continuity_ui`, `test_trust_receipts_api`,
`test_get_started`, and `test_ui_a11y_dark_mode`.

The earlier checkpoint cohort consists of `test_workflow_builder`,
`test_workflows_index`, `test_vendor_onboarding_api`, `test_outcome_pilots`,
`test_privacy_ops_api`, `test_flow_action_episodes_api`, and
`test_flow_recipe_lineage_api`, with `test_get_started` included in the secure
rerun. These cohorts overlap; their counts must not be added as unique tests.

An important evidence correction: earlier dashboard-only runs labelled secure
set `MAVERICK_TEST_SECURE_DEFAULT=1` alone. That switch is consumed by the core
test package's conftest, while the repository root defaults
`MAVERICK_SECURE_DEFAULT` to `0`. Those earlier labels therefore do not prove
secure posture. The final secure runs explicitly set **both** variables to `1`;
ordinary runs explicitly set `MAVERICK_SECURE_DEFAULT=0`.

Final results, completed at 00:38 UTC:

| Check | Result | Task-local audit log |
| --- | --- | --- |
| Nine-module follow-up, ordinary | 125 passed, 73.25 seconds | `visible-qualified-ordinary.log` |
| Nine-module follow-up, both secure flags | 125 passed, 55.06 seconds | `visible-qualified-secure.log` |
| Earlier cohort plus get-started, both secure flags | 245 passed, 356.38 seconds | `workflow-production-posture.log` |
| Sharing lifecycle browser-DOM tests | 5 passed | `node --test packages/maverick-dashboard/tests/js/deliverable_sharing_lifecycle.test.js` |
| Changed Python files and helper | Ruff passed | `python -m ruff check` |
| Patch whitespace | Passed | `git diff --check` |

Python emitted only the existing Starlette/AnyIO deprecation warning. Earlier
intermediate logs contain the superseded markup assertions and are not final
qualification evidence. The new tests used no skip or suppression.
