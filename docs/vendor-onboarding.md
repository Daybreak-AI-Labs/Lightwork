# AI vendor onboarding and evidence renewal

This bounded workflow obtains a versioned Google Drive text file, builds an AI
vendor diligence assessment, records human corrections and approval, creates a
Jira Task, verifies it, and records a separate customer acceptance decision.
A renewal creates a linked case, assessment and ticket while retaining the
previous evidence and decisions. Acceptance never releases a vendor or model.

The implementation was developed against `74366121e4c569a8139de2f039b53717c0471cd3`.
Offline synthetic tests establish the supported behavior. Google Drive, Jira,
identity-provider, customer and Kubernetes restore acceptance remain unverified.

## Run the offline demonstration

Use Python 3.10–3.12 and install the monorepo's eight packages as documented in
`AGENTS.md` / `.devcontainer/post-create.sh`. The repository root is a workspace,
not an installable Python package. Verify that `maverick.__file__` resolves to
this checkout before running tests.

```bash
python3 scripts/demo_vendor_onboarding.py \
  --state-dir /path/to/new-empty-demo-state \
  --report /path/to/demo-results.json

# Optional loopback UI; leave the process running while using the browser.
python3 scripts/demo_vendor_onboarding.py \
  --state-dir /path/to/another-new-empty-demo-state \
  --serve --port 8766
```

On Windows use the isolated environment's `python3.exe` launcher and Windows
paths. Each invocation requires a new or empty state directory. The script
refuses to overwrite existing state. Stop its local server with Ctrl+C.

Open `http://127.0.0.1:8766/third-party/vendor-onboarding`. This loopback-only
harness uses a synthetic reviewer and fake integrations. Its banner identifies
the simulation. It does not run the production application's background jobs,
call an LLM, contact a provider, or establish customer acceptance. Do not expose
this harness on a network. The production application uses normal authentication
and tenant selection.

The script exercises first onboarding, promotion and definition rollback of a
renewal classifier, a linked renewal using a new document version, and a lost
ticket-create response recovered through read-only reconciliation. The emitted
JSON includes actual domain records and measured evaluation results. To create
another synthetic request in the UI, use Drive file ID `security-paper` and Jira
project key `VEND`; the evidence deliberately leaves diligence gaps.

## Configuration and enrollment

The installer asks whether to enable the workflow and for its resource owner.
It remains disabled by default. For an existing installation, explicitly add:

```toml
[vendor_onboarding]
enable = true
resource_owner = "procurement"
document_connection = "gdrive"
ticket_connection = "jira"

[vendor_management]
enable = true
[ai_vendor_risk]
enable = true
[model_risk_assurance]
enable = true
[evidence_graph]
enable = true
[connections]
enable = true

# Optional: required for explicit deterministic renewal-flow promotion/run.
[flows]
enable = true
```

Keep the installation's existing authentication, encryption, safety and egress
policy. These settings do not enroll credentials or broaden connector grants.
Unknown onboarding keys, malformed settings and unavailable configuration are
rejected. Both credential references must be distinct and explicitly enrolled.

Use the existing Connections management surface to enroll two tenant-local,
sealed records with the selected names: connector `gdrive` at the pinned HTTPS
origin `https://www.googleapis.com`, and connector `jira` at the enrolled HTTPS
origin used by the existing Jira integration. Both records must have the same
nonempty owner (credential authority), current revisions, and access grants for
the named reviewer. The Jira adapter uses a Bearer token and the existing Cloud
v3 issue operations. Validate the actual tenant's token type, endpoint, scopes,
project permissions and egress policy during live acceptance; these tests do
not establish that an arbitrary Jira token or hosting configuration works.

Open **Third parties → AI Vendor Onboarding** as a named authenticated user with
`operate` permission and an active tenant. Anonymous sessions and a shared static
dashboard token do not provide a named human reviewer. The request composition
derives tenant, actor, configured resource owner and enrolled credential
authority; JSON bodies cannot supply those fields or a `human` assertion.
Current authority is checked again at sensitive service boundaries. The bounded
dashboard composition binds approval and execution to the same named principal;
it is not a general delegated or multi-reviewer approval engine.

## Supported operations and decisions

| Step | Supported behavior | Boundary |
|---|---|---|
| Intake | Vendor, intended use, Drive file ID, Jira project key, 1–730 day evidence validity, optional previous case | Tenant and resource owner come from validated context |
| Evidence | Ordinary UTF-8 `text/plain` Drive file, at most 128 KiB; metadata before/after download; version, timestamp, hash and citations | Native Google Docs, PDF, binary files and multi-document synthesis require a human-managed path |
| Assessment | Existing AI vendor-risk questions, conservative source extraction, explicit unknowns, gaps and findings | Vendor assertions are not independent verification; no automatic compliance or model-quality determination |
| Review | Typed answer/note corrections, reviewer, rationale, superseded assessment/evidence references, reported review time | A model context cannot approve; unresolved gaps may remain explicitly recorded |
| External action | One exact Jira Task create, read-back and marker search using existing protocols | No update/delete/bulk action, automatic retry after ambiguity or automatic reversal |
| Outcome | Separate accepted/rejected decision, external confirmation, renewal date, elapsed time, cost status and history | Execution completed, external verified, customer accepted and released are distinct states; release stays false |
| Renewal | Linked new intake; separately collected evidence, review, approval and ticket | No autonomous ticket creation or automatic vendor authorization |
| Deterministic step | Promoted, restricted renewal-readiness classification using current case/provider state | A classification cannot approve, write a ticket, or broaden permissions |

Missing, empty, corrupted and unavailable evidence remain distinct. Collection
failure does not create an empty successful assessment. Live execution cost is
unmeasured; fake execution cost is zero. Browser review time is reported elapsed
time, not a verified measure of a person's active work. Scripted review duration
is explicitly unmeasured. Customer-specific records and learned behavior remain
tenant-scoped; reusable evaluation material is internally authored synthetic
data. This feature does not pool customer data.

## Architecture and recovery contract

`vendor_onboarding_api.py` owns closed request schemas and HTTP responses.
`vendor_onboarding_composition.py` owns request authentication, current tenant,
resource owner and credential enrollment. `VendorOnboardingService` owns state
transitions and domain decisions. `ServiceContext` is an immutable validated
boundary object. Google Drive/Jira adapters own the existing provider protocols
and exact credential selection, using existing egress and SSRF protections.

The existing vendor register and AI vendor-risk assessment register remain
systems of record. Tenant-local `GovernedRecordStore` namespaces coordinate
cases, operations and improvement evidence; they do not replace those domain
registers. Existing governed-action execution and flow definition stores are
reused. The dashboard renders provider and assessment text as text, not HTML.

Each operation binds durable ID, tenant, actor, credential references, exact
payload digest, scope, evidence identity/freshness, vendor/assessment versions,
credential generations and an expiring human approval. Execution rechecks them
before and after claiming the operation. The actual Jira HTTP request resolves
and compares the approved credential generation. Verification checks the
project, type, summary, exact description, operation marker and a usable external
updated version. Receipt creation alone is insufficient.

Jira Task create has no assumed provider idempotency. The durable executing
claim precedes the call. Duplicate delivery after a claim, a timeout, uncertain
receipt persistence, or process interruption enters reconciliation. Read-only
marker search must find exactly one matching ticket and verified payload.
Missing, multiple, mismatched or unreadable results remain blocked. In particular,
zero search results do not authorize another create: indexing may lag. Reconcile
after verifying authority and provider state; an operator must resolve ambiguous
duplicates or unsupported states through the provider's normal process.

There is no universal exactly-once guarantee or automatic compensation. A Drive
file can change after the final precondition check but before Jira commits; the
providers do not share a transaction. Approval is for the recorded evidence
version and exact ticket content, not an eternal claim about current source
truth. Read-back and acceptance may also observe subsequent provider changes.

Corrections validate before domain writes. Recovery reuses a corrected
assessment only when answers, reviewer, decision and rationale match exactly.
Acceptance links the authoritative assessment recoverably and separately from
the external action. Revision conflicts require reloading and reviewing current
state. Failed storage or verification never supplies approval or acceptance.

For the runtime repairs, see [runtime accounting and recovery](runtime-accounting-recovery.md).
Restoration preserves the live Budget object and current limits; setup,
cancellation, exceptions and final output refusal converge on lifecycle
settlement. Checkpoint settlement metadata prevents ordinary replay charging.
**The quota JSON ledger and checkpoint SQLite acknowledgement are separate
stores:** a crash between those writes can conservatively double-charge history;
hard loss can lose uncheckpointed provider usage. Crash-atomic provider billing
is not implemented. See [evidence and transport boundaries](evidence-reliability-boundaries.md)
for strict scanner failure, explicit tenant erasure and event replay semantics.

## Reviewed deterministic renewal step

`EvidenceRenewalEvolution` extends `flow/evolve.py` for one typed step: classify
accepted evidence as `fresh`, `renewal_due` or `human_fallback`. The restricted
graph uses existing scope, subtraction, assignment and branch actions. It adds
no external tools or authority. Reviewed bindings name the exact `due_at` and
`now` inputs; a tool name alone cannot establish replayable parameters.

The executable example derives observations from an accepted case and current
provider state. It records typed inputs, reviewed output, rationale and source
references; proposes a candidate from at least three distinct reviewed inputs;
evaluates held-out fresh/due cases, changed inputs, invalid bindings, invalid
types and changed downstream versions; and requires explicit human promotion.
Promotion retains the exact candidate revision, evaluation and bindings digests,
flow version and generation. Later evaluation does not rewrite that approval.
Rollback creates a new definition version restoring the prior agent definition
and disables the active compiled pointer. It does not undo tickets.

The dashboard's **Check renewal readiness** invokes the approved definition
against server-derived current evidence and ticket versions. A disabled flow,
missing promotion, unsupported input, rejected/unaccepted case or changed state
retains a human/agent fallback. **Start renewal request** still requires the
normal human-reviewed workflow. Observation, proposal, evaluation, promotion and
rollback are explicit application-service operations illustrated by the runnable
script; the dashboard does not autonomously train or promote definitions.

The baseline comparison is an explicitly labeled deterministic stand-in for
agent/human renewal judgment using the same guards and reviewed bindings. It is
not an LLM benchmark or proof of an improvement learned from real agent traces.
The technical contribution is the functioning path from reviewed case evidence
and explicit bindings to a restricted executable flow, evaluated and promoted
with immutable provenance and rollback. No patent novelty, production readiness,
customer savings or model-accuracy improvement is claimed.

## Migration, persistent deployment and restore

Existing APIs are preserved; onboarding endpoints are additive. Configuration
defaults to disabled. New workflow record namespaces and additive checkpoint JSON
settlement metadata need no destructive schema migration. Existing vendor/risk
records remain readable. Back up state before enabling the feature. Disabling
the feature retains records and effects; reverting code does not remove tickets
or erase customer decisions. Do not resume a checkpoint under an older writer
that ignores its settlement metadata without reconciling quota history.

The selected deployment path is the existing **single-replica Helm chart**.
It now rejects disabled persistence even with PostgreSQL, a state mount other
than `/home/maverick/.maverick`, a conflicting `env.MAVERICK_HOME`, multiple
replicas and autoscaling. PostgreSQL centralizes the world model only; approvals,
credentials, tenant keys, checkpoints, audits and flow definitions still need the
state volume. Keep externally supplied Secrets free of state-path overrides:
Helm cannot inspect their contents. The AWS examples were not converted into a
supported durable path by this change.

```bash
helm lint deploy/helm/maverick
python3 scripts/check_vendor_onboarding_helm.py --helm helm \
  --output /path/to/helm-validation.json --artifacts /path/to/rendered-manifests
```

Before a real restore rehearsal:

1. Quiesce requests, workers and scheduled writes; stop the single application
   writer. Preserve outstanding operations and their current provider status.
2. Take a consistent snapshot/backup of the entire mounted state root, including
   SQLite databases and their journals, tenant encryption keys, sealed credential
   records, audits, governed records, flow definitions and checkpoints. A live
   file-by-file copy is not a consistent SQLite backup.
3. If using PostgreSQL, take a coordinated database backup and record the
   application version, configuration and snapshot pairing. Apply existing key
   custody and backup access controls; a database-only restore is insufficient.
4. Restore the matched state and database into an isolated single-writer
   installation of the same application version. Verify tenant ownership,
   decryption, named enrollment, domain records, approvals, flow versions and
   checkpoint readability before allowing writes.
5. Reconcile every executing/ambiguous external operation against the provider.
   Verify a known receipt and a interrupted-operation scenario. Do not clear
   claims or recreate tickets to make a restore look successful. Re-enable
   service only after the rehearsal's evidence and remaining exceptions are
   reviewed by the operator.

Automated evidence includes chart rendering/refusal checks and a fresh-process
test that exits after a fake provider durably writes a ticket, then restarts and
reconciles without another create. This is local storage/process recovery,
**not** proof of a Kubernetes PVC restore, disaster recovery objective or live
provider consistency.

## Focused validation

```bash
python3 -m pytest -q packages/maverick-core/tests/test_run_lifecycle_reliability.py
python3 -m pytest -q packages/maverick-core/tests/test_vendor_onboarding*.py
python3 -m pytest -q packages/maverick-core/tests/test_vendor_evidence_evolution.py
python3 -m pytest -q packages/maverick-dashboard/tests/test_vendor_onboarding_api.py
python3 -m pytest -q packages/maverick-dashboard/tests/test_event_transport_parity.py
python -m ruff check .
python -m vulture
```

On shells that do not expand test globs, pass the explicit test filenames.
The delivery report records full-suite and repository-gate results, skips,
baseline exceptions and platform-specific limits. Live acceptance must separately
exercise enrolled Drive/Jira permissions, identity revocation, actual version
semantics, rate limits, ambiguous provider success and persistent restore before
any production-readiness claim.
