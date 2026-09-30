# Owned saved views and pins in My Data

This follow-on closes the existing UX-store inventory gap identified during the
LW019–042 acceptance reconciliation. It extends the existing My Data journey and
governed deletion service; it does not introduce another storage or authority
model. It supersedes the unresolved My Data integration note for LW041 in
`settings-final-qualification.md` and `slices/settings.json`.

My Data lists content-free counts and an exact owner/storage-bound snapshot for
saved dashboard views and pinned task references. A separate private download
contains only that named account's currently stored filter records and task
references. It never dereferences task IDs into source content. The existing
ten-minute sealed download rechecks account, workspace, current membership and
export audit acknowledgement at download time. Native and typed Copilot export
choices name the same store.

The user can include this store in an independently reviewed deletion request.
Existing preservation holds, distinct current administrator review, durable
dispatch-before-effect state and per-store audit acknowledgement remain required.
The core store compares the exact reviewed owner snapshot under its writer lock,
removes only that owner's `views` and `pins`, then reads back the result. New pins
or views invalidate a pending review. Other accounts, task content, trace
annotations and gallery records are retained. Request metadata contains counts
and an opaque revision, never private saved-query text or titles.

Malformed/unreadable storage now raises for exact inventory, export and erasure;
it cannot become a false empty inventory. Interrupted writes or unacknowledged
audit progress remain unconfirmed/partial and are never automatically repeated.
Content-free deletion receipts remain in the existing personal-data-request
namespace; no new erasure receipt store is introduced.

## Required consolidated source boundary

Root identified that the historical singleton could write one workspace's UX
records into another workspace's first-opened file. The root-owned correction
changes both the default constructor and shared factory to **ux_store-v2.json**
and retains old UX files as untrusted legacy lineage, without guessed migration.
This adapter operates only on the current shared store and must be integrated
with that correction. It never scans, imports or erases the old filename. Legacy
UX files remain an explicit separate retention exception. This isolated
checkpoint does not claim that an uncorrected legacy file is safe to export.

## Local evidence

On isolated source based on b89320c, 71 focused local Python cases passed across
the new core erasure/native adapter tests, existing UX store tests, My Data
requests/exports/Copilot and saved-view cases. These cover exact-owner and
physical-workspace snapshot separation, content-free review, stale review,
distinct reviewer, retained shared records, write interruption, pending audit,
private export context and malformed-storage refusal. The current trusted-store
filename migration is independently owned and qualified by root as noted above.

Eleven existing/new My Data controller cases passed, including explicit store
choices/counts/retention, account/context revocation clearing and preservation of
unfinished work on ordinary failures. Ruff, JavaScript syntax and whitespace
checks passed. Synthetic controller and in-process filesystem regressions are
not an installed distributed/multi-process qualification. No provider call,
dependency mutation, production write, deployment or merge was performed.
