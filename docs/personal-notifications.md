# Personal in-product notifications

Your preferences links to **Your notifications**. With personal preferences and
`[notifications] personal_alerts` enabled, named accounts can choose immediate
alerts, a daily digest, event subscriptions, a time zone, quiet hours, project
watches and temporary task mutes. Installation administrators can enable this
in All settings; the installer provides the same choice.

The in-product inbox refreshes every 30 seconds while visible. Administrators
can also enable `personal_background` in All settings or the installer. The
dashboard then observes explicitly enrolled accounts while their pages are
closed, releasing eligible digests at the next observation. Opening the inbox
enrolls that named account in an encrypted tenant-local registry. The UI shows
the last completed background observation; enabled is not described as delivered.
The worker shares the dashboard shutdown supervisor, active-tenant checks,
current named-account authority, and the complete source projection (including
budget warnings). Revoked membership stops observation and removes that
account's enrollment and inbox state. At most 100 recipients are observed per
cycle with a rotating cursor and 20-second work budget; busy deployments may
observe less frequently than 30 seconds. Organization webhook alerts keep their
separate policy.

Administrators can additionally enable `personal_external` and save an HTTPS
dashboard origin in `personal_dashboard_url`. The installer exposes both. A
person first proves and confirms a messaging identity in My Data, then selects
that verified identity in personal notification preferences. Slack and Telegram
private delivery reuse the configured channel bot and current sender allowlist;
public room IDs and organization webhook destinations are refused. Delivery
checks the bot account digest against the authenticated receipt that proved the
link, and rechecks current identity/link/policy under the authority fence.

Messages carry a count and exact authenticated task/approval links, without task
titles, results or decision content. Opening links uses ordinary dashboard
authentication. Slack opens a one-person conversation and checks its returned
recipient before posting; Telegram accepts positive private sender IDs only.
The provider mechanisms follow [Slack conversations.open](https://docs.slack.dev/reference/methods/conversations.open/)
and [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).

Encrypted per-person/per-verified-link delivery records atomically claim semantic
item IDs before sending. Accepted, refused, interrupted and uncertain sends are
not automatically replayed. The inbox shows private delivery failures and service
acceptance separately from its explicit Mark seen action. One provider send is
attempted per recipient observation, oldest-attempted destination first. Daily
digests claim all currently eligible unseen delivery IDs once per local day;
critical exceptions still require both administrator policy and user opt-in.
Records keep up to 1,000 consumed item IDs for seven days; capacity pauses new
sends rather than evicting duplicate protection. Revoking a link stops its use.

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

Remaining: durable event ingestion for transitions between observations, complete
history pagination, personal preference Copilot coverage and installed/provider
qualification. Mock-only core policy/receipt/private-delivery/offline-policy
tests passed 20/20, followed by 8/8 private-delivery regressions after path and scope hardening after the user authorized local tests. No live messages were
sent during implementation or validation.
