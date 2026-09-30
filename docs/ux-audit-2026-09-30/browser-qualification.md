# Local installed-browser qualification — LW-149

This is local qualification evidence, not a claim of live-provider, IdP, packaged
desktop or customer acceptance. The audit entry remains `test-needed` for those
boundaries and the final consolidated build. No production configuration,
credential or provider account was changed.

## Environment and evidence

Windows, installed Chrome 154, Playwright 1.58.2, Python 3.12.12. The isolated
Python environment imports all eight packages from the same browser checkout;
the restore subprocess resolves the identical application build after scrubbing
`PYTHONPATH`. Each server owns disposable configuration and local encrypted
stores, binds loopback and blocks outbound sockets before product imports.
The browser blocks requests outside its fixture origin. OAuth token responses,
knowledge evidence, captured result and accounting admissions are synthetic.

The completed baseline run used integrated `4bd5557` plus browser changes through
`8e8d2bb`: 64 of 69 cases passed in 14 minutes. The remaining cases were preserved
as failures, investigated and rerun:

| Journey | Finding and verified outcome |
| --- | --- |
| Snapshot and retained restore receipt | The generic ten-second UI wait ended while real snapshot verification was still running. Wait for the exact successful native snapshot/drill response, each bounded at 60 seconds, then retain the ordinary ten-second UI assertions. Targeted journey passed in 16.9 seconds. |
| Two admitted pilot cohorts | Eight real admissions, repeated independent reads, baseline correction and exact export took 49 seconds. A bounded 90-second journey budget passed with assertions unchanged. |
| Help follow-up layout | The page now has three supported follow-up groups. Assert all three, including the new search form, and check each against the floating button. Passed in 2.5 seconds. |
| Task file recovery | Restricted Windows process could not hold native parent-directory handles. The unchanged guarded production implementation passed in an authorized unrestricted local fixture in 3.8 seconds. Alias and race guards remain enforced. |
| Independent protected-pilot review | An independent administrator correctly lacks ambient source-task access. The test now asserts initial denial, requests each exact source with purpose and five-minute expiry, rejects requester self-approval, and obtains owner approval with acknowledged audit receipts. The GET succeeds. The subsequent review POST exposed a separate source-read/mutation distinction; final qualification awaits that repair. |

The retained-key custody journey passed with the matching checkout interpreter.
The earlier build-identity conflict from mixing two checkouts was not bypassed.
Per-run local logs and trace/screenshot folders are under the executor's `audit/`
directory (`browser-slice-full3`, `browser-recovery-targeted2`,
`browser-pilot-reviewed-access`, `browser-enterprise-thirteenth`). Generated
runtime artifacts are not committed as application data.

## New enterprise lifecycle

`enterprise_lifecycle.spec.js` passed in 55 seconds using actual single-use local
invitations and signed cookies for five distinct roles: installation
administrator, workspace administrator, employee, manager and auditor.

The journey verifies invite replay and expiration; installation controls denied
to ordinary and workspace-scoped roles; no ambient access to the employee's
private task; an employee authorizing only an approved own-account profile;
explicit provider-return confirmation; provider-application changes refused;
colleague connection isolation; governed source inspection/export/correction;
owner-selected captured result sharing and independent manager approval;
verified personal export; real departure with retention/handoff uncertainty
shown; departed-session denial and clearing of previously displayed personal
data; and native auditor session revocation.

The captured result explicitly says that model quality and live retrieval are
unqualified. It is fixture evidence for sharing and approval controls. It is not
an observed model-generated grounded answer. Local accounting tests establish
cost evidence handling, not a live provider invoice reconciliation.

This journey found two production regressions: approved employee self-connect
was blocked by the page visibility floor, and My Data retained private rendered
state after actual departed-session denial. Page policy now reflects the
administrator's approved self-connect policy while respecting explicit page
restrictions; native application management remains administrator-only. My Data
clears values, private previews and downloaded-link controls and locks actions on
401/403, redirects or proven account/workspace mismatch. Transient errors and
ordinary revision conflicts preserve unfinished edits. Seven DOM regressions
cover those distinctions. Ten native connection/workspace workflow tests and the
own-connect visibility regression pass.

The earlier asset sweep exercises all 174 literal advertised JS/CSS references
through actual HTTP responses and MIME checks; 23 missing explicit routes were
restored. Ordinary task record search no longer scans every unrelated domain
pack. Browser navigation, appearance, current authorization and search passed in
the complete local run.

## Remaining qualification

Run the independent-pilot regression after its source-read repair and rerun the
final consolidated commit. Real IdP login/session expiry, approved live provider
consent/refresh/revocation and outages, provider-grounded model output, actual
paid-cost reconciliation, packaged desktop/native OS behavior and customer
acceptance require explicitly approved environments and accounts. None were
called or inferred from these local fixtures. Preserve these limits in the PR
and release decision.
