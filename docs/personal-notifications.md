# Personal in-product notifications

Your preferences links to **Your notifications**. With personal preferences and
`[notifications] personal_alerts` enabled, named accounts can choose immediate
alerts, a daily digest, event subscriptions, a time zone, quiet hours, project
watches and temporary task mutes. Installation administrators can enable this
in All settings; the installer provides the same choice.

The delivery channel currently implemented is the in-product inbox. It refreshes
every 30 seconds while its page is visible and catches up when reopened. There
is no request-less scheduler or external per-person delivery routing yet. The
UI states that limitation. Organization webhook alerts keep their separate
policy and are not repurposed as personal destinations. This is partial LW141
implementation, not a claim that phone/email digests are complete.

The projection reads the latest 200 owned tasks and a bounded assigned-approval
list. Coverage limits are displayed. Semantic status/question/result versions
are deduplicated per named account in encrypted, tenant-scoped state. Current
authorization is checked before projection/publication; missing sources are
removed and current labels are hydrated anew, never stored in alert history.
Deep links open the ordinary authenticated task/approval pages. Fetching an
alert does not mark it seen; the person uses **Mark seen**.

Digest release uses the person's IANA wall-clock time and remembers the local
delivery date. A skipped DST clock time releases at the first eligible later
refresh; a repeated hour does not produce a second daily digest. Quiet hours
continue to apply. Approval/deadline timing exceptions require both the admin
`personal_critical_bypass` policy and the person's explicit opt-in. Mandatory
organization operational alerts are not controlled by these preferences.

Preferences use the shared account store's revision and account/workspace
binding. Background reads retain unsaved form edits; conflicts require an
explicit reload before editing the latest values. Muting one task does not
mute other tasks. Unsubscribed/off periods do not queue a later replay.

Remaining: background event ingestion (including transitions between page
refreshes), external recipient/channel binding and delivery receipts, complete
history pagination, shared Copilot operations and runtime qualification. The
authored DST, recipient-isolation, deduplication, mute and revocation regression
tests have not been executed.
