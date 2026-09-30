# Reviewed Copilot controls

The shared registry is `maverick_dashboard.product_operations`. Adapter modules expose `entries()` and only registered native services can run. Page metadata does not grant permission. Every snapshot/prepare/execute/recover must authorize its exact source through native services. Enabling `[features] copilot_operations = true` opens only the families selected in `[copilot] operation_families`; the default is `["learning"]`. Features and All settings control the same policy, and the installer asks for both choices. Native page controls remain independently available.

The model can propose one advertised control ID. It receives only static labels/IDs and the user's question; source details, current preferences, content and execution arguments are excluded. Selection opens a typed form. Its validated arguments produce a sealed ten-minute preparation bound to current source revision, account/workspace/access and enabled families. A separate checkbox and apply control are mandatory. Execution checks the same native authority and revision under the shared authority fence. Native durable receipts recover exact prior operations. Uncertain responses never automatically retry.

## Adapter contract

- `Snapshot(revision, source, summary, details={}, fields=[], blocked="")` contains only currently authorized data. Fields use text, textarea, number, boolean or typed select choices; dependent enum choices are supported.
- `Preview(summary, changes=[], native={})` is the exact native plan. For read-only adapters, `native` is the result projection shown to the person. For mutating adapters, it stays inside the encrypted preparation.
- `Outcome(status, message, details={}, background=None, delete_cookies=())` supports confirmed, replayed, queued or unconfirmed. Async native services can be called through `anyio.from_thread.run` from the FastAPI worker callback. Native dispatch may return `BackgroundTasks`; do not create an independent dispatcher or retry a paid run.
- `recover` checks the native durable receipt before revision rejection; it never infers success merely from a coincidentally matching final value.

`lightwork-product-operations.js` is shared by both shells. On chat pages the Copilot button lives in the sidebar so it does not cover the composer. Other surfaces may select an exact source with `lightwork:copilot-selection` and `{selection: ...}`; changing it clears earlier context and review. Chat supplies `{kind:"goal",id:N,goal_id:N}`. This selection is untrusted and every adapter validates it.

## Current evidence and integration limits

This checkpoint implements the registry, typed review UI, configuration, model proposal boundary and native scoped preference adapter. Native learning-example review remains on its existing guarded adapter and respects the selected learning family. Connection/schedule, notification, budget and task/clarification/review/artifact adapters are parallel worker deliverables; absence of a module never advertises a placeholder capability. LW-002 remains partial until the complete accepted matrix is integrated and checked.

Focused local Python tests: 17 passed, covering source/context/access changes, confirmation/tampering, forbidden extra arguments, read-only projection, exact receipt replay and preference inheritance. Static AST, JavaScript syntax and targeted Ruff passed. No live provider calls occurred. Full integrated suites and browser interaction checks remain to be completed by the parent task.
