# LW-028 / LW-029 task-map acceptance closure

Reviewed against integrated source `87c5df110b1884e33a619ad09f2e234ece004b7b`.
The native map already used atomic revision comparison, but its Copilot rename
and move adapters and actual page-selection binding were missing.

`product_operations_task_map.py` now supplies `tasks.rename` and `tasks.move`
through the same native API functions and SQLite/PostgreSQL transaction. Typed
choices contain current owned tasks only, exclude cycles and closed work, and
require an explicit review. Structure receipts use the existing
`goal_mutation_receipts` store, kind `structure`, with actor, operation identity,
exact request hash and sealed historical acknowledgement. Recovery does not
apply a prior edit over a later revision or claim that its historical title is
still current. A reviewed content read does not grant map mutation authority.

The map publishes its real selected task to Copilot. Requests have a bounded
deadline; submitted fields are locked; uncertain outcomes keep the draft and
never trigger an automatic mutation retry. An explicit refresh preserves the
draft while showing current saved state. Child retries retain the same durable
operation identity across uncertain acknowledgements. Changed workspace context
aborts pending requests, clears Copilot selection and leaves stale controls locked.

The core transaction also prevents a map move from removing or replacing an
inherited handoff spending ceiling. Descendants must stay in their current
governed continuation; ordinary moves remain available. Existing continuation
roots retain their own durable ceiling. Source and destination handoff closure
checks run inside the transaction. No new budget is allocated or copied.

Local validation: the initial native map/core/adapter batch passed 29 tests;
the additional inherited-ceiling regression passed in the subsequent 63-test
settings acceptance batch. All 30 distinct native map/core/adapter checks pass.
Five jsdom tests run the real graph controller and cover current selection,
duplicate-click fencing, uncertain child retry, stale draft comparison and
workspace change. Ruff and whitespace checks pass. PostgreSQL multi-process
qualification and final installed keyboard/browser integration remain separate.

The parent must register `product_operations_task_map` in the explicit lazy
product-operation catalog when integrating this checkpoint. This uses the
existing configured `tasks` family and its installer/admin controls.
