# Employee companions: first increment

Implementation date: September 30, 2026. **Draft, untested.** The user requested
that testing follow implementation. This document describes code boundaries,
not deployment, security qualification, or customer acceptance evidence.

## What this increment provides

An employee has a personal companion home at **My companion** (`/companion`).
They can ask “what is running?” or “what needs attention?” while their existing
tasks continue. Replies use the existing conversational-supervisor grammar and
current world-model task records. Task-status replies include current source
links when records are available, observation times and an owner/policy decision
receipt. Help replies and empty results have no task sources; restored history
does not restore previously authorized links. This is a deterministic, read-only
supervision conversation: it makes no model requests and starts no agent work.

The existing `ChatThreads`, runner, task queue, submission receipts, task
approval and cancellation behavior are unchanged. This separate conversation
does not acquire the active chat's single-turn admission fence. It does not
steer an active agent, start another task, or become a replacement agent runtime.

The existing installation provider configuration is untouched. This increment
does not read connector credentials, search enterprise documents, invoke tools,
learn preferences, or send audio or text to an external provider.

## Setup and operation

1. An installation administrator enables **Employee companions** in Settings.
   The optional installer choice sets the same default-off feature switch.
2. The employee and the managing administrator must have explicit membership
   in the selected workspace and named signed-in accounts. The historical
   authenticated-user default role does not enroll anyone.
3. A workspace administrator opens **Employee companions**
   (`/companion/admin`), chooses a current workspace member, and enables their
   companion. The permitted source is **Their own tasks** and the permitted
   operation is **Read task status**. Either can be revoked.
4. The administrator chooses no saved history (default), or 1, 7 or 30 days.
   The employee can clear their own saved history. Disabling the employee's
   companion or selecting no history deletes its live retained history as part
   of the policy update. It does not erase task records or backups.

Policy edits compare the revision the administrator reviewed with the current
revision. A stale form refuses to overwrite newer policy. Browser requests are
also bound to the signed-in account and selected workspace, and mutation routes
require the existing same-origin check. Role, membership, offboarding, suite
access and policy are re-read under the installation's authority-effect fence.

## Data and authority boundaries

- Task queries always specify the current employee owner; returned rows are
  checked again. Bootstrap administrator owner/department bypass helpers are
  deliberately not used. Current department restrictions still apply. Tasks
  attached to a project require that project to have the same employee owner.
- Task views are bounded to 50 visible nonterminal/attention records. They are
  snapshots, not an assertion that every task or event has been scanned.
- A new private companion message is never inserted into ordinary goals,
  conversations, facts, shared memory, or the general audit writer. This avoids
  giving legacy goal-content access paths a copy of the companion conversation.
- Retained messages and replies use the existing tenant-and-owner-bound
  authenticated encryption helper. Plaintext history fallback is refused.
  No-retention mode stores only the metadata event, not the exchange text.
- Admin APIs return only policy and event metadata. No administrator route
  accepts an employee ID to retrieve their conversation. There is no privileged
  content-access process in this increment.
- Each policy change or completed companion reply is published atomically with
  its required local metadata receipt. Failed persistence does not return a
  successful reply. The journal records actor, owner, action, revision and time;
  it excludes prompts, replies and task titles. Refused requests are not a
  complete refusal-audit stream.
- The feature's local file authority is bound to a named tenant. Stores refuse
  tenant-context changes, corrupt records, unsupported capability expansion and
  a configured replica count other than one. A maximum of 1,000 employee
  records, 50 exchanges per employee, and 200 metadata events per employee keeps
  the preview bounded. Admin activity shows the latest 200 workspace events.

The administrator remains able to change retention policy. Applying a shorter
retention period can rewrite encrypted history internally, but no content is
returned to the administrator. Increasing retention cannot resurrect previously
expired or removed history. History expires on employee access or policy
updates; there is no scheduled purge worker. Already-rendered browser text and
backups have separate lifecycles. The browser stores no companion text in local
storage and clears its view when it detects an account/workspace change.

## Remaining work and honest limits

This is **not the complete dot-style companion**. Free-form model conversation,
draft generation, cross-session learned memory, connector/document retrieval,
write approvals, active-run steering/cancellation, notifications and live voice
remain deferred. Source ACL propagation must be qualified before adding
enterprise retrieval. Model/provider routing must be reviewed before adding
model-generated companion replies. Every external effect will need current
authority checks and exact approval/replay semantics at its execution boundary.

Privacy here applies to the new companion conversation. Existing administrator
access to ordinary tasks/chats and general audit content has not been removed.
This feature does not establish deployment-wide employee conversation privacy,
no-egress guarantees, compliance, or privileged-host confidentiality. Installation
administrators and operating-system/key holders remain trusted. The metadata
journal is bounded and mutable, not an immutable compliance archive.

The authority is host-local; shared PostgreSQL policy storage, replicated
deployment coordination, scheduled deletion, backup erasure, and retirement of
offboarded employees' retained files are unfinished. A deployment that omits its
replica-count declaration is not proof of single-host operation. Keep this
preview disabled in distributed deployments until that storage work is complete.

Global Copilot has page guidance only for this feature. It cannot enroll an
employee, change policy, answer from companion state, or clear history. These
missing operations and unverified browser/recovery journeys mean the customer
experience contract is only partially implemented.

## Qualification to perform later

Regression tests have been authored for policy validation, revision conflicts,
tenant and employee isolation, admin metadata privacy, retention, deletion,
persistence failure, current membership/revocation and API/UI behavior. They have
**not been executed**. No model call, provider acceptance, browser render,
authenticated installation journey, lint, full suite, or secure-default run is
claimed by this PR.

After preparing an isolated supported Python environment and confirming local
package imports, run the added core, dashboard and installer tests, then the
relevant existing authentication/chat/settings regressions. Repeat under
`MAVERICK_TEST_SECURE_DEFAULT=1`; run Ruff, Vulture and repository contract gates.
Review desktop/narrow layouts, keyboard use, stale tabs, policy revocation,
retention changes, missing encryption support, corrupted stores and account
switches in an authenticated local application before enabling the feature.
