# Customer readiness review — September 14, 2026

**Decision: complete customer readiness is not established.** The product has
substantial implemented functionality and strong automated regression evidence.
There are still release, deployment and live-workflow acceptance gaps that
prevent treating the whole platform as a qualified, self-service customer
delivery. This review found additional reproducible defects after the previous
audit; passing tests must never be described as finding every possible error.

The review starts from main `d10e800d7d45551eabb56890e48296f67d73a952`, which
contains merged PR #2512. Work is isolated on
`codex/customer-acceptance-20260914`. Both Windows self-hosted and managed AWS
delivery are assessed. No customer deployment, model-provider account, external
business-system account, production signing identity or customer data was used.
Research/lab and companion prototypes are not counted as accepted customer
capabilities.

## New defects and corrections

| Finding | What a customer experienced | Correction and verification |
| --- | --- | --- |
| Local owner/invite sign-out | A newly enrolled owner had no Sign out control. `/auth/logout` returned 404 and retained the session cookie. The same route rejected SAML-only deployments. | The shell and route now share the supported browser-session policy. Local owner, invite and SAML sessions can clear their cookie; same-origin sign-out-everywhere verifies the relevant session signature before revocation. OIDC end-session behavior stays covered. SAML sign-out ends the Lightwork session; upstream SAML single logout is not implemented here. Eleven new sign-out cases reproduced the defect before the fix. |
| Global Opt-Out setup failures | With the module disabled, configuration/history/integrity/maintenance paths could throw an internal server error while constructing their store, before the operation error handler ran. | Store construction and the operation run inside the same worker-thread/error boundary. Disabled modules return the existing structured 403 contract; unavailable authority returns 503. Actual owner-account requests and store-construction/thread regressions cover this. No feature or authority is enabled by the fix. |
| Consent destination setup failure | A fresh customer opening destination integrity/reconciliation paths could receive 500 because the underlying consent delivery store was disabled. | Destination-store construction uses the ordinary delivery API's configuration gate. Disabled delivery returns actionable 403 guidance. Real fresh-account tests cover integrity and ambiguous-operation reads. |
| Incomplete/incorrect installation commands | The desktop source recipe installed only two packages; the single-client wheel recipe used distribution names that do not match the repository manifests. Config initialization and standby restore commands could run under the administrator's home rather than the service account. | Recipes include the eight-package cohort, the actual `maverick-agent` and `maverick-mcp-server` names, and the appropriate runtime extras. Initialization and recovery now name the service identity/home/binding. They retain reviewed-source/offline-wheelhouse boundaries. |
| Incorrect systemd credential example | The token variable was assigned `%d/dashboard_token`, which is a filename, not the secret file's contents. An operator following it would configure the wrong bearer value. | The example now uses a root-owned mode-0600 environment file provisioned by the host secret manager. This was reviewed against systemd environment-file semantics; the unit was not installed on a live Linux host. |
| Recovery/offboarding interpretation traps | The runbook claimed raw signing keys were in normal backups, `--force` could override a newer schema, and erasing the tenant root proved complete erasure of all client data. The public operations guide also assumed the legacy shared database path and recommended direct database replacement. | Documentation now uses the actual service scope and authenticated restore, requires separate key custody, keeps forward-schema refusal explicit, and inventories global administration, extra workspaces, external stores and offsite copies. A verified archive alone does not prove recovery of encrypted application records. |
| Outdated release/deployment claims | Documentation still said the native Windows workflow had never passed, described Windows as a planned MSIX/PyOxidizer delivery, and claimed silent local Firecracker fallback. | It now distinguishes the implemented Tauri/PyInstaller MSI candidate and successful synthetic CI from missing customer release acceptance. The unsupported local Firecracker scaffold is described accurately. Performance gates are separated from customer service-level commitments. |

## What was actually exercised

### Follow-up: interrupted saves and access changes

The additional failure-recovery pass reproduced four further defect families.
The table groups related failures across the affected customer paths.
This batch is based on main `2f93808897f89caed311e12fffb7e31648d2073d`,
after the initial review and repairs were merged in #2513, and is developed on
`codex/customer-recovery-20260914`.

| Failure | Customer impact | Repair and regression evidence |
| --- | --- | --- |
| Failed lock acquisition stranded previously acquired locks | A recoverable file error or cancellation could leave later workspace, membership, invitation, trigger, trust, federation or voice-store operations stuck. | Seventeen helpers now unwind partial acquisition and transfer successfully acquired guards to the caller. The original lock order and strict-lock flags remain intact. Tests retain the failure traceback, verify cleanup after both I/O errors and cancellation, then acquire again. An actual obstructed workspace lock file is removed and the public selection operation successfully saves and clears state without restarting. |
| Blocking administration handlers | A slow Settings save, role/grant operation, user-list read or invitation email could stall unrelated requests on the same dashboard event loop. | Eighteen affected handlers now use synchronous worker routes or explicitly await worker-thread I/O. Nineteen concurrent-request scenarios include the already-correct provider probe as a control; they check that another request completes before the injected slow operation is released and that the tenant context is preserved. No live email or provider request is made by these tests. |
| Partially applied Settings selections | Capability and feature forms published one intermediate configuration per checkbox. File errors and malformed section data produced raw 500 responses. | Each form now commits its full selection under one settings lock and one atomic file replacement. Tests observe only the complete new selection, preserve the original file when storage is unavailable, retry successfully, and retain malformed data for recovery with a 503 response. Existing single-switch callers retain their interface. |
| Stale workspace-member screen after access loss | The loaded roster and pending colleague identity remained visible after a 401/403, and editing controls were re-enabled. | The controller clears private state and locks further actions until a fresh authenticated page load. Twelve response cases cover refresh/save/remove, both status codes and JSON/HTML bodies. Temporary 503 failures continue to preserve the editable form for retry. |

The lock suite passed 37 cases, the administration recovery suite passed 25,
and the complete dashboard DOM suite passed 757. A broader affected-store suite
passed 557 cases in each of normal and secure-default configurations. The
administration/permission suites passed 237 cases in each configuration after
their static scanner was extended to recognize worker-thread writes. Seven
serial concurrency cases and 104 benchmark/verifier tests also passed. These
suites overlap and include passing controls; their counts must not be added as
distinct defects or treated as live customer acceptance. The final full CI and
native acceptance runs for the published revision are linked in the pull request.

CI subsequently identified the desktop updater's Rustls 0.23.43 dependency as
affected by [GHSA-2mjx-qc3c-rqvc / RUSTSEC-2026-0285](https://github.com/rustls/rustls/security/advisories/GHSA-2mjx-qc3c-rqvc),
published on September 14. The upstream fix rejects TLS 1.3 handshake messages
crossing encryption-level boundaries. Cargo updated this dependency to the
patched 0.23.45 release; the locked Windows dependency graph resolves that
version through the updater's Reqwest/Tokio/Rustls stack. The advisory remains
enforced by CI. Native tests and dependency audit results for the updated lock
are recorded in the PR.

### Initial fresh-customer pass

The Python environment independently installs all eight packages from this
checkout. Imported core/dashboard module paths were checked before testing.

- **Fresh-install service sweep:** actual first-owner form enrollment and signed
  session, new local stores, default configuration and blocked external network
  access. It requested **122 registered pages**, **9 additional local links** and
  **438 literal GET API paths** discovered from the actual OpenAPI document.
  It excludes routes needing resource IDs, streaming/export endpoints and
  mutation operations; it is not coverage of every endpoint or every feature.
- Before repair, three API requests returned 500 and local logout returned 404
  with its cookie still present. After repair, the same sweep has **zero 500s**
  and logout returns 303 with the session removed. The page results are 120
  responses at 200 and two explicit disabled-feature refusals at 403. API results
  are 165 at 200, 223 at 403, 24 at 422, 11 at 503, two at 409, one at 401 and
  12 at 404. The 503s report disabled or unavailable optional authority/runtime
  configuration; they are **not** passing acceptance for those modules.
- **Targeted regression tests:** 211 passed in normal configuration and 211
  passed with `MAVERICK_TEST_SECURE_DEFAULT=1`. These overlap; they are not 422
  distinct tests. Coverage includes owner/invite/OIDC/SAML sessions, current
  permissions, tenant selection, project review, consent composition and the
  repaired paths. A test-isolation issue in the new fixture was corrected by
  clearing its cached client binding between cases.
- **Rendered browser coverage:** the existing Chromium suite uses actual
  dashboard controllers/stores with synthetic identities and provider adapters.
  The added fresh-owner navigation case uses actual enrollment and default
  configuration. It passed navigation through **all 66 rendered sidebar
  destinations**, with no captured JavaScript exceptions or HTTP 500 responses,
  and sign-out at a 320-pixel viewport with no horizontal overflow. The phone
  screenshot was visually inspected. Navigation waits for shell initialization;
  it does not claim that every workflow or background request completed.
  All 60 pre-existing main journeys and seven optional setup cases passed across
  the local suite and focused reruns. Final CI run links are recorded in the PR.
- **Repository gates:** Ruff, Vulture and all **17 offline repository gates
  passed**: plugin matrix, specialist catalog, reachability, dispatch, egress,
  audit refusals, deprecations, gRPC, accessibility, async routes, failure policy,
  schema migrations, migration governance, regulatory compiler, evaluator
  anchors, control/data end-to-end, and control/data soak. The latter two use
  synthetic model behavior. Final CI status belongs to the immutable runs linked
  in the PR, not to this document's presence.

Native-return browser cases require the installed dashboard origin
`127.0.0.1:8765`. Their passing rerun used that origin. The fresh-owner navigation
case uses its own temporary installation and actual browser-origin headers.

The clean main baseline has a successful [25-job CI run](https://github.com/Daybreak-AI-Labs/Lightwork/actions/runs/34794118847),
including the actual Python 3.10–3.12 and production-security-posture slices.
The earlier [full matrix for the code merged in #2512](https://github.com/Daybreak-AI-Labs/Lightwork/actions/runs/34777429758)
passed 29,396 ordinary cases per complete interpreter/posture run, plus the
separate serial concurrency phase, native document tests and live PostgreSQL
tests. This is regression evidence, not proof of customer installation or a
real model/business-system transaction.

## Acceptance matrix

| Customer need | Repository/local evidence | Still required for customer acceptance |
| --- | --- | --- |
| Receive, install and open Windows | Eight-package frozen MSI, offline WebView2 bundle and native maintenance implementation; [synthetic native CI passed](https://github.com/Daybreak-AI-Labs/Lightwork/actions/runs/34777432941). | Actual publisher identity, distributable immutable artifact/channel and a clean supported Windows machine. Exercise first owner, ordinary launch, repair, offline bootstrap and interrupted operations on that artifact. |
| Upgrade and recover an installed release | Native synthetic install/repair/failed-upgrade/completed-rollback tests; separate [real release-pair gate](../windows-release-acceptance.md) exists. | No prior published release exists in GitHub as of this review. Establish explicit first-release acceptance; do not invent a prior release. Later qualify two genuinely different runtime versions, actual signatures and supported data migration/rollback. |
| Run on AWS | Terraform validation, application image build, scoped executor credential validation and Kubernetes worker/recovery contracts. | Provision and qualify the [external executor](../../deploy/aws/EXECUTOR.md), image, cluster/CNI, namespace credentials, secrets, DNS/TLS, backup and monitoring. Prove network isolation and real file delivery; no AWS apply or cluster acceptance was performed here. |
| Set up an owner and teammates | Actual local enrollment, invitations, tenant membership, current role/suite checks and session handling. Sign-out defects repaired. | Actual customer identity lifecycle: owner custody, invite delivery, join/revoke, expired sessions and their chosen SSO/SCIM configuration. A local email label is not verified email ownership. |
| Connect company accounts | Named connection registry, OAuth return/reconnect/rotation, source checks and exact credential-version binding have local regression coverage. | Consent and actual access with the customer's chosen account types and scopes. Test refresh/revocation and a bounded authorized read/write/readback. Synthetic Google/Drive/Jira/ServiceNow responses are not that evidence. |
| Complete AI work and deliver a usable result | Goal dispatch, worker execution, budgets, approvals, result review, task-file recovery and retry accounting have extensive local tests. | On the intended deployment, run representative tasks with a real selected model, real documents and the agreed tools. Verify output quality, sources, human approval, cancellation, budget exhaustion and actual delivery. No live model call was made in this review. |
| Privacy, consent and compliance operations | Governed stores, review/approval/revision contracts and local public-consent round trips; three fresh-config errors repaired. | Exact customer modules must be configured and tested. A 200 HTML page, a green status fixture or a control mapping does not qualify the customer's live property, privacy-rights process or regulator/auditor outcome. |
| Autonomous shoring | Trusted task/approval attribution, full-row aggregation and completion timestamps are implemented and regression-tested in #2512. | Observe a real baseline, completed work, retained review effort, corrections and costs. Validate throughput/capacity and savings with the customer. Platform counters alone do not prove headcount or expenditure reductions. |
| Learning and improvement | In-process learning and separate higher-authority promotion controls exist; local/synthetic evaluation and rollback evidence. | Held-out representative customer work, evaluation separation, reviewer ownership, a bounded promotion and a demonstrated rollback. The existence of learning code does not establish measured improvement for this customer. |
| Business documents | Native CI exercised Calc formulas, OCR and bounded workbook/VBA-part preservation; parser libraries are included in the full runtime. | Configure the native document worker image and test representative workbooks, scans and exports. Arbitrary Excel macro execution/compatibility is not established. |
| Costs, usage and purchased capabilities | Durable usage receipts, reservations, retries and expiry-aware rate ceilings; offline signed platform licenses support an omitted expiry for perpetual use. | Validate the issued license, trusted publisher key, purchased tier/suites and per-workspace limits. Reconcile actual provider charges. Decide updates/support terms separately from software use; the standalone-agent `LW1` token system is a different licensing path. |
| Restore after machine loss | Authenticated local snapshots, explicit custody/restore drills, retained-key verification and protected-state refusal. | Restore on a separate host with independently retained keys/configuration; recover any external databases; resume a real workflow and verify protected spending/action history. Measure the actual recovery time and acceptable data loss. |
| Operate without routine assistance | Help/support bundles, setup feedback, recovery UI and administrator controls exist; source/runbook errors corrected. | A customer operator must complete installation, first value, reconnect, update and recovery using the delivered instructions. Verify desktop sleep/closure behavior, an agreed workload/soak, disk pressure, alert delivery and the escalation route. These have not received an independent customer usability sign-off. |
| Offboard and export | Bound tenant export/erase and signed audit controls exist. | Inventory and verify removal/retention across tenant roots, separate workspaces, global account/configuration state, external systems and offsite archives. Do not promise that one erase command covers them all. |

## Release blockers and the evidence that closes them

| ID | Required work | Accountable owner | Closure evidence |
| --- | --- | --- | --- |
| R1 | Prepare the actual customer release, signing trust and distribution. | Release owner | Reviewed source, immutable artifact/checksums, customer publisher identity, protected signing/acceptance environments and clean-machine first-release results. GitHub currently has no releases or recorded real release-pair run; only `github-pages` and `pypi` environments were returned. |
| R2 | Qualify each deployment being offered. | Deployment operator | Windows evidence under R1; for AWS, a running isolated control plane and qualified executor with recorded image/cluster/CNI identities, isolation denials, timeout and file-recovery results. |
| R3 | Prove the selected model and account integrations in the intended environment. | Product owner plus customer account administrator | Approved test accounts, exact scopes/configuration, successful authorized work and readback, denied access, expired credentials and safe reconnect/retry without duplicate side effects. |
| R4 | Demonstrate useful work and accepted outcomes. | Workflow owner and customer reviewer | Representative tasks with expected outcomes, observed corrections/effort/cost, accepted output and explicit limitations. Cover the business workflows being sold, not just vendor onboarding. |
| R5 | Rehearse full recovery and establish operations. | Deployment/customer administrator | Separate-host restore, successful decryption, recovered configuration/external state, spending/action reconciliation, measured recovery time, monitoring evidence and named escalation ownership. |
| R6 | Finish the self-service delivery and commercial configuration. | Product/release owner | Customer-level instructions, independently observed first-run/reconnect/recovery, correct perpetual platform entitlement where sold, purchased modules/limits, update policy and separate support terms. The repo's proprietary license requires a separate agreement; it is not itself a completed customer order. |

R1–R6 are acceptance work, not assertions of six newly discovered code
vulnerabilities. The repository API also returned no branch protection and no
rulesets during this review. Establish the required review/check policy before
relying on it to control release promotion; this review does not change those
repository settings.

No production deployment or release is approved by this report. The fixes can
be reviewed and merged independently while these acceptance gaps remain open.
An explicitly scoped design-partner trial needs acceptance of its own actual
installation and workflows; it is not evidence that the entire platform is
ready for unrestricted customer delivery.
