# Reviewed employee departure

Draft implementation; regression and installed acceptance tests remain unrun.

Enable `features.employee_departures` in Settings or the installer. A named
installation administrator can open **Users → Review employee departure**.
The reviewed inventory includes exact-owner tasks, projects, pending approvals,
project review packets, run-goal schedules, local owned connections and role
memberships in the main workspace and registered tenants. No task title or
private message is needed for this inventory. The supported task window is
5,000 per workspace; a larger inventory prevents the action.

Starting a departure requires a fresh inventory fingerprint, a different active
administrator as retained-data custodian and explicit preservation confirmation.
The workflow refuses self-deactivation and administrator deactivation until a
separate role change. It records the review, retires the exact named identity
through the existing SCIM/session boundary, removes the reviewed generations of
local connection credentials and cancels still-pending run-goal jobs. It records
dispatch before each effect and confirmation afterward. An interrupted dispatch
has an unknown outcome; an audit retry does not blindly repeat the effect.

Every case remains `handoff_required`. Running work, provider-side consent,
external identity accounts, and other module queues need separate closure.
Per-item retained, transfer-reported and revocation-reported dispositions name
an active administrator and preserve a human evidence reference. Those reports
are visibly human attestations, not provider verification. They never grant
private-content authority. Existing records and holds are retained; this action
does not erase personal preferences or make a legal-hold determination.

The UI, API, feature flag and installer expose actual supported effects. Copilot
page guidance explains the controls. Executing departure operations through the
global Copilot remains an integration gap, as does verified closure across every
module/provider and safe resume of an uncertain interrupted effect. These gaps
must remain open in LW-094; the presence of a page is not complete offboarding.
