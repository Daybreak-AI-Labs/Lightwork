# Saved task-filter views — LW041

The final acceptance review found that UxStore and `/api/v1/views` already stored
owned named views, but Tasks had no complete control for using them. This change
extends that store and those endpoints, rather than introducing another copy.

Tasks now saves the current text search and status under a name, explicitly
reviews replacement/removal, lists owned views, and opens a generated Tasks URL.
Keyboard-accessible controls accompany the existing account pin ordering and
start-page preferences. The existing personal-preferences feature gate controls
this journey; its installer question now names saved task filters. A named
account is required. Task access is rechecked by the ordinary task page/search.
Views apply to the native bounded result set: 200 recent tasks or up to 100 search
matches. No exhaustive all-history claim is made.

The native service and typed Copilot operations share validation, account and
workspace context, an allowlist of query/status parameters, explicit confirmation
and optional lock-atomic compare-and-swap on the existing UX store. Copilot entries
are `task_views.status`, `task_views.save` and `task_views.remove` in the
preferences family, supplied by `product_operations_task_views`. The integrating
branch must register that module in its explicit lazy catalog. Legacy dashboard
views remain supported; a task view cannot overwrite an incompatible legacy view
whose fields were not shown in the review.

Requests and response parsing have a 15-second deadline, an owned cancellation
controller, duplicate-submit fencing and scope-change clearing. An uncertain
mutation retains the name and offers read-only refresh. The older view store has
no operation receipts: recovery accurately reports current state and does not
claim that a particular historical request succeeded or resend it automatically.
The shared UX store remains deployment-local JSON persistence; this change does
not claim distributed storage or cross-process writer qualification.

Local checks on the isolated source based on 87c5df1:

- Eight new native/service/API tests plus existing UX endpoint and store tests:
  29 passed. After the legacy-name collision guard, all eight focused tests passed
  again. These include two concurrent writers against one reviewed revision,
  current account isolation, feature refusal, exact native asset response,
  allowlisted links, restart persistence and truthful Copilot uncertain recovery.
- Six new jsdom saved-view cases plus editor recovery, in-app forms and All
  Settings page cases: 41 passed. The workflow recovery fixture now supplies its
  localized product-text helper; all unfinished-field and whitespace assertions
  remain unchanged.
- Ruff for changed Python files and Git whitespace checks passed.

The browser-controller tests execute shipped JavaScript with synthetic HTTP
responses. They are distinct from installed authenticated browser qualification.
No provider calls, deployment, production data or dependency changes were used.
