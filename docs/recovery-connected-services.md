# Review restored connections

Workspace recovery keeps old connected-service work on hold. An administrator
can review an item in the recovery list or open the same review in Recovery
Copilot. Changing between those views preserves one form and one set of entries.
This local helper does not contact an AI provider or start ordinary work.

For messages, record whether the requested action happened, whether the message
was delivered, and the findings for each reply part. Previously confirmed
deliveries and their receipts must be retained. For collection, the form shows
the position or time required by that connection. Check the original service,
enter when it was checked, and supply a reference to the evidence. Unknown
outcomes remain unresolved; no choice is silently treated as “did not happen.”

Review the details and explicitly confirm to record them. These are your
findings, not independent verification by Lightwork. New observations must be
within 15 minutes and no earlier than restoration. Recording a review never
repeats the old work. Returning to ordinary use is a separate decision after
all held items have been reviewed.

If recording was interrupted, refresh the review. When its findings were
already saved, Lightwork shows those saved findings and offers to finish
recording the same review. It does not change the observation time or send
the old action again. A lost response is not success: refresh status before
taking another action.

Entries remain in memory during same-account refreshes when the reviewed item
has not changed. Confirmation is cleared. A different recovery, changed item,
changed account or lost permission invalidates the earlier review. There is no
browser-storage copy of these entries.

## Current product boundary

The structured forms replace the old requirement to edit a data object or
enter numeric timestamps. Manual source-position numbers are still required
for some collection sources, including iMessage. Finding those numbers can
still require technical knowledge; an in-product source lookup or selection
journey is outstanding. Other recovery controls are not yet operated by the
local helper. The full recovery page still needs language and visual review.

Native file and archive tests, API admission tests and synthetic browser checks
are separate from authenticated installed-application acceptance. These changes
do not establish that every post-install operation is complete.

## Implementation and qualification

The existing `features.workspace_recovery` setting, installer step and global
administrator admission remain in force. The typed operation is confined to
the recovery route family and uses the existing locked reconciliation service.
Ordinary Copilot/model routes remain unavailable in recovery mode.

The service binds a form to the original authenticated recovery and item. It
derives record identities internally, checks the current source and preserves
the native no-replay validator. Interrupted publication uses the exact retained
readback and digest, including older readbacks with timestamps that a date
control cannot reproduce exactly. Signed bootstrap disposition and retained
readback evidence are not a claim of a new independent external audit receipt.

Relevant implementation:
[form adapter](../packages/maverick-core/maverick/channel_recovery_controls.py),
[locked service](../packages/maverick-core/maverick/workspace_recovery.py),
[typed API](../packages/maverick-dashboard/maverick_dashboard/recovery_api.py),
[shared form](../packages/maverick-dashboard/maverick_dashboard/static/lightwork-workspace-recovery.js),
and [local helper](../packages/maverick-dashboard/maverick_dashboard/static/lightwork-recovery-copilot.js).
