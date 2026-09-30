# Settings and save integrity checkpoint

This checkpoint implements the save-integrity services and adds a scoped preference foundation. The per-ID disposition is in `slices/settings.json`; planned rows describe concrete missing work. Nothing in this checkpoint is runtime-qualified: the owner asked to build first and test later. Regression tests are authored and remain unrun. No provider calls, deployment, or production changes were made.

## Durable mutation contracts

Deliverable edits now require `expected_updated_at` and a client `operation_id`. Only finished (`done`) results are editable. SQLite and PostgreSQL use one transaction for version comparison, result-only mutation, immutable prior/current artifacts, sign-off invalidation and a durable encrypted receipt. Retrying an acknowledged-or-uncertain operation returns the original revision. Task-map rename/move requires the reviewed timestamp. Child creation requires an operation ID and persists its child receipt atomically. Migration 42 adds the receipts table; migrations 1–41 and their checksums are unchanged.

All Settings requires `expected_revision`, an opaque commitment to installer, dashboard, deployment and relevant environment sources. Validation, current source resolution, write, audit, and failure rollback run under the same process and cross-process writer fence. The review endpoint uses the same validation/authority code. Imported profiles, presets and history restores write through this path. Other dirty rows stay in the browser. Submitted controls are locked until a response settles.

Prepared settings-file installation binds exact reviewed bytes, account/workspace and effective revision for 15 minutes. A private journal records reviewed, pending, uncertain and confirmed states. The result action compares installed bytes; explicit reconciliation records that observation without repeating an uncertain install. Backups use a random suffix. Activity logs contain only opaque review references and status, never file bodies.

## Account preferences

`[features] personal_preferences = true` enables the preference page and named-account persistence; the installer asks about it. Records are encrypted, private and tenant/principal scoped. Organization and workspace defaults are distinct from personal and conversation overrides. Every save is revision checked. Organization defaults require global administration; workspace defaults require workspace administration. Personal names, favorites and layout cannot be shared defaults.

Optional installation policy is `[personalization] locked_fields = ["tone", "output_style"]` with a `[personalization.defaults]` table for locked values. These are presentation controls, never tool permissions, credentials, approval rules or memory collection. A personal/device override cannot bypass these locks. Policy editing UI remains tracked under LW-030.

The account runtime adapter only reads verified dashboard-channel principals. Conversation styles use the existing `ChatThreads` membership binding. It does not guess a Slack/Teams/CLI identity from a matching user string. Global `persona.user_name` is no longer injected. An administrator with a named account can explicitly migrate a legacy name in `/preferences`; anonymous and service work receives no personal name. `erase_personal(principal)` is available for verified identity erasure, but channel-to-account erasure mapping is not inferred.

Display changes apply immediately without navigation. Account defaults travel across devices; “Save on this device only” writes context-bound cookies. System theme responds to operating-system changes, explicit themes win, and text size has standard/large/larger presets. The shared picker in both shells includes Undo appearance. These changes also supply LW-044 and the rollback portion of LW-045; the accessibility slice owns those dispositions.

`[features] configuration_profiles = true` enables reviewed portable profiles and outcome presets; the installer asks about it. Profile version 1 intentionally exports only supported voice/display/budget fields. Provider credentials, personal names, paths, policy documents and deployment identities are excluded. Dedicated model-route portability and personal-style bundles remain tracked, not represented as implemented.

## Integration and remaining work

The companion/conversation slice also changes the deliverable response's truthful learning fields and the follow-up composer. Preserve its `saved`, `learning_observation` and `training_completed: false` fields when combining with this checkpoint's durable revision receipt. Successful follow-up navigation should await `form.mvRecovery.clear()` and dispatch `lightwork:composer-sent` with `recoveryCleared: true`; inputs must remain locked through the acknowledged send.

The notification slice is adding its adapter to `experience_preferences.DEFAULTS`, the preference page and actual delivery consumers. Root's next wave owns Today pending-decision/upcoming-occurrence projections and template defaults/pins. Memory-use narrowing is blocked on verified principal-to-channel/store ownership adapters; no placeholder toggle is exposed.

Further remaining items are exact dedicated-settings focus/search coverage; runtime-applied revision acknowledgements; table-reset semantics that can mask inherited installer rows; full portable model/style bundles; authorized Copilot preference journeys; and complete verified-identity erasure integration.

New required revision/operation fields intentionally reject stale legacy mutation clients. Existing catalog/graph/deliverable test fixtures must be migrated to send reviewed versions before the deferred test run. Added regression files cover result/graph CAS and receipts, preference identity/inheritance/locks, settings conflicts, file review binding, shared style registry, safe profile import/export and device-cookie isolation. Ruff and Jinja2 were not installed in this executor; no dependency installation was attempted solely to run deferred checks.
