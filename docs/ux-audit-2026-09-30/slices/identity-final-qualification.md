# Identity and personal-data acceptance reconciliation

Reviewed the original acceptance criteria for **LW-091–104, LW-107–109 and
LW-112**, against integrated source
`9bea7d4e4c751b022cf5f9d6b261c8af6f025d6c` and the bounded LW-112 correction
`43da36e8b30012023c81501f77c3307a61dc97b7`. The source backlog is unchanged:
these eighteen entries retain their original nine code-supported, eight proposal
and one test-needed evidence classifications. Implementation status is a separate
field. This is not a new security assessment or certification of every route.

## Ledger precedence

Apply `identity-final-qualification.json` **after** the original `identity.json`,
`personal-memory.json`, and historical slice notes for these exact eighteen IDs.
The two original files have also been refreshed to avoid restoring their obsolete
“authored tests remain unrun” and “all installed-browser acceptance unrun” text.
No other IDs or aggregate/source files are changed here. Later immutable final
suite and browser results should supersede this qualification snapshot.

Every row deliberately remains `implemented-test-needed`: the implementation is
present, but the remaining field names specific acceptance boundaries still to
qualify. A successful local component run does not indiscriminately validate an
entire audit criterion. In particular, the existing 30-case implementation-era
batch never established every keyboard, mixed-retention-setting or live SSO case.

## Feasible implementation gap found and closed

My Data previously grouped local selected-result bundles and responsibility
handoffs with unverified external derivatives. Those local stores have native
ownership and lifecycle services, so that grouping omitted knowable dependencies
and controls required by LW-112 criterion 2.

`personal_shared_inventory.py` now calls the existing authorized service
factories, inboxes and individual record readers under the authority fence. It
shows only exact-owned, currently readable metadata: the saved record, original
source link when still owned, named recipient, use, access/offer expiry,
independent dependencies and native review/export/revoke/withdraw links. It does
not return captured bytes, private review comments, instructions or a recipient's
independent successor details. An accepted handoff does not transfer erasure or
source-content authority back to its sender.

The native inboxes retain their existing bound; My Data inspects at most 100
currently authorized records and labels the incomplete window. Disabled features,
unavailable storage or refused authority yield **unavailable**, without a zero
count. Received copies are not relabelled as owned data. Revocation and expiry
are explicitly different from physical deletion. Local retained exceptions now
have separate entries from external knowledge/model/copy limits. The existing
shared Copilot personal-data status action uses this same native inventory.

The handoff deep link performs only a fresh authorized read. It neither accepts
a handoff nor starts work. No additional feasible implementation gap was found
in this bounded eighteen-entry review; this is not a claim that unrelated
product surfaces are free of defects.

## Executed local checks

All checks used local disposable fixtures and synthetic adapters. No live provider,
IdP, production, deployment, credential rotation or PR push was performed.

| Evidence | Exact result and scope |
| --- | --- |
| Current identity acceptance | **62 passed**, Python 3.12, against `43da36e`. Complete files: `test_identity_audit_regressions`, `test_first_login_defaults`, `test_permission_requests`, `test_employee_departures`, `test_employee_content_privacy`, `test_retention_review_receipts`, `test_product_operations_identity`, and core `test_personal_data_controls`. Log: `audit/identity-final-acceptance.log`. |
| Current local-copy and personal-data check | **59 passed** against `43da36e`: complete `test_personal_shared_inventory`, `test_personal_data_api`, `test_personal_data_requests`, `test_product_operations_personal_data`, core `test_deliverable_bundles`, and core `test_task_handoffs`. Log: `audit/identity-local-copies-final.log`. |
| Current personal-data controllers | **15 passed**: `personal_data.test.js`, `personal_data_access_loss.test.js`, and `task_handoffs.test.js`; includes exact deep-link reads, denial, literal-text rendering and clearing private state on authority loss. |
| Current task authority controller | **4 passed**: initial read-only controls, fresh reauthorization, mid-session project-choice revocation preserving its value, and normal select interaction. |
| Final earlier core memory/privacy rerun | **205 passed, 2 skipped** at `c79e969`: learning store/guard, adapter rung, fleet memory, user notes, context memory, GDPR CLI matching, erasure coverage, text export, OIDC server, run sharing, training identity, and domain discipline. The two `test_adapter_rung` skips require Windows symlink creation unavailable to that process. Log: `audit/identity-core-final.log`. This supersedes the older 204-pass intermediate note. |
| Earlier learning/policy correction | **158 passed** in the complete affected learning-store, erasure, harness CLI/operator and dashboard learning/policy/signals files. The larger prior harness run had **622 passed, 7 skipped, 10 failed**; all ten were in CLI/operator files subsequently corrected and included in this 158-pass run. Do not present it as one clean full-family rerun. Log: `audit/identity-checkpoint-final.log`. |
| Earlier metadata/privacy correction | Initial batch **145 passed, 2 failed**; corrected assertions then passed in a **47-pass** run of complete oversight/control/learning-loop files. Updated owner scoping separately **22 passed**. Logs: `audit/identity-metadata-current2.log`, `audit/identity-oversight-final.log`, `audit/identity-authz-integrated-final.log`. |
| Static delta | Changed Python passes Ruff; both changed controllers pass Node syntax; whitespace checks pass. No ignored lint-rule suppression was added. |

The shared Node dependency initially was not on this fresh clone's search path;
the two jsdom-dependent files did not load. Reusing the existing local dependency
directory resolved that environment error, after which all fifteen cases passed.
This was not a product assertion failure. The source/fixture counts above are
separate overlapping runs and must not be added into an invented unique total.

## Installed-browser evidence already available

The committed [browser qualification](../browser-qualification.md) records
Windows Chrome 154 / Playwright 1.58.2 with all eight packages from one disposable
checkout and outbound sockets blocked. The original 69-case run passed 64 and
preserved five failures; each correction then passed its targeted rerun. A new
five-role enterprise lifecycle also passed. That is **70 distinct local journeys
across full and targeted runs**, not one final-commit 70-case pass.

For these IDs, the enterprise journey supplies actual invited-cookie evidence
for installation/workspace authority separation, no ambient employee-task reads,
approved own-account connection scope, owner-selected result sharing, personal
export, departure, expired authority and clearing old private DOM. The separate
protected-pilot journey initially denies an independent administrator, requires
four exact owner-approved five-minute content grants with acknowledged receipts,
then allows audited reads and refuses stale evidence. It grants no task mutation.

These runs resolve the old blanket claim that authenticated installed-browser
acceptance was never attempted. They do not resolve every per-row browser case.

## Criterion-by-criterion limits

| ID | Implemented acceptance boundary reviewed | Remaining qualification |
| --- | --- | --- |
| LW-091 | Empty required role choices; built-in/custom power preview; no preselected admin. | Keyboard submission and each custom-role form in the final installed build. |
| LW-092 | Actual standard/group fallback, power preview, revision-bound reset and honest success text. | Installed viewer-to-operator/custom-role before/after confirmation. |
| LW-093 | Workspace membership controls do not load or render global roster/editors; global controls stay separately gated. | Final browser rerun and remaining membership edit matrix. |
| LW-094 | Reviewed departure cannot close without every current item and external boundary having an active custodian; holds and uncertain dispatch remain explicit. | Concurrent running-work/provider consent boundaries; external revocation requires its custodian's real evidence. |
| LW-095 | Policy administration separated from employee content; exact expiring reviewed reads and receipts; audit metadata projection before search/export. | Final integrated/browser run and separate newly reported privacy/security claims below. |
| LW-096 | Explanation follows actual channel/user/role root ceiling and correct independent policy owner; review alone grants nothing. | Final installed denial/permission-change journey; separate resource/consent/provider gates remain distinct. |
| LW-097 | Fresh conservative recommendation; explicit viewer/no-departments migration with revision/readback/audit rollback. | Packaged install/upgrade and deployment override matrix. |
| LW-098 | Account uses effective enterprise enforcement separately from the profile label, exposing override. | Exact profile-plus-mode-false account/readiness installed comparison. |
| LW-099 | Persistent workspace context, change guard and pinned-deployment refusal. | Actual two-tab draft/approval workspace-switch browser scenario. |
| LW-100 | Initial server-side task controls plus fresh owner/permission checks, including project submission. | Final authenticated viewer/member/admin and mid-session role-change matrix. |
| LW-101 | Safe styled failure, local-only return and fresh sign-in without old-code replay. | Controlled IdP failure/expiry/outage/invalid-state plus narrow keyboard interaction. |
| LW-102 | Explicit null means no pruning, omission means preserve; effective readback detects forced overrides. | Native all-clear/mixed-clear, reload/enforcement and override acceptance now pass in both postures; see ../remaining-acceptance-qualification.md. Rendered browser interaction remains. |
| LW-103 | Current policy/scope dry run, holds/dependencies, dirty-form invalidation and fresh fenced authority. | Installed global-admin-with-active-tenant workflow and PostgreSQL; external holds remain attested. |
| LW-104 | Persistent skipped/failed/unknown store outcomes, partial reporting and unresolved-only retry. | OS-specific interruption/locked-file and PostgreSQL qualification. |
| LW-107 | Source or explicit legacy/manual provenance, revision-bound correction, durable suppression and private audit. | Installed correction/reconsolidation journey; model recall quality is not established. |
| LW-108 | Verified tenant/account/sender links, explicit authoritative-source choice and unlink stopping future sharing. | Live adapter proof and final installed cross-channel link/conflict/unlink journey. |
| LW-109 | Personal recall, organization reuse and source-bound future training are distinct; ceilings/holds retained. | Installed comprehension/control journey; no claim of prior-model unlearning. |
| LW-112 | Per-store owned data/export/reviewed erasure; explicit retained/held/unknown exceptions; now local bundle/handoff provenance and controls. | Final integrated/browser/PostgreSQL results; external copies and previous training require separate custodian processes. |

## Separate unresolved claims

The parent has recorded credible severe claims from PR2530 outside this original
195-entry implementation inventory: feedback/annotation workspace bleed,
arbitrary flow-secret references, vendor hero markup, and same-identifier tenant
reuse. This change does not implement or qualify them. In particular, this
document must **not** be used to claim that all privacy is qualified while those
separate findings remain open. The parent owns their explicit scope and
disposition. No authorization to merge, deploy or make production changes is
implied by these local results.
