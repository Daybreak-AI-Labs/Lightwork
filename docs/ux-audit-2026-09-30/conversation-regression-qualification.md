# Conversation and workflow local regression qualification

Source snapshot: integrated commit `f008354` (including the native conversation inbox and task/schedule recovery), followed by this checkpoint. Run on Windows with Python 3.12, all eight isolated package source roots on PYTHONPATH, and a short, private pytest temporary root. No live provider, production, credential, merge or deployment action was performed.

The initial 355-test focused run had 298 passes and 57 failures. Review identified a real ownership regression: newly started auth-off workflow runs stored the audit actor label `operator` as their owner, while history correctly filtered to unowned work. New runs now store the current employee principal, or an empty owner for genuinely local work. Historical reserved local labels are recognized only when authentication is genuinely off, no execution identity exists, and the immutable root and complete child bundle prove unowned provenance. Named/token callers and missing or foreign provenance remain denied. Flow event streams recheck permission and exact current ownership on every snapshot.

The shared Copilot partial now provides its absent shell flag default under StrictUndefined. Reviewed-content, account and personal-data destinations are registered with the existing view permission floor. Native service permissions remain authoritative.

The remaining regressions required updating fixtures to current product contracts: authored templates have explicit owners; graph edits supply reviewed revisions and child operation IDs; stream tests use actual Request objects; Stop carries no answer-quality judgment; administrators have no implicit employee-content access; answer assertions follow the external controller and localized accessible label.

## Results

Results below are the final result for each file after focused reruns, rather than adding overlapping suite totals:

| Local group | Passing cases |
| --- | ---: |
| Flow API | 102 |
| Webhook trigger API | 28 |
| Event transport parity | 18 |
| Event triggers | 30 |
| Graph editor | 19 |
| Governance shell/Copilot integration | 18 |
| Sign-off, explicit feedback and export | 22 |
| New local workflow ownership/provenance and stream revocation | 11 |
| Conversation, attachments, inbox, clarification, organization, task operations, handoffs, read projections and answer accessibility | 143 |

Total distinct cases in these groups: **391 passed**. The untouched core `test_flow_payloads.py` also passed all 13 cases in the initial current-source run after shortening the Windows pytest root; the previous long-path failures did not require weakening storage or ownership checks.

Local artifacts under the executor `audit` directory: `conversation-regressions-f008.xml`, `conversation-regressions-fixed1.xml`, `conversation-regressions-fixed2.xml`, `conversation-flow-ownership.xml`, `conversation-owned-regressions.xml`, and `conversation-ui-projections.xml`, with matching logs. Earlier reports retain intermediate failures; subsequent targeted reports resolve them as described above.

Ruff, Python syntax review and git whitespace checks cover every changed Python file. One upstream Starlette/AnyIO deprecation warning remains. This is local regression evidence; full integrated qualification, native PostgreSQL/distributed leases, and installed authenticated browser acceptance remain with the integration owner. No external-provider acceptance is claimed.

## Additional task and schedule contracts

Follow-up source snapshot: root integration `2f7cc40`, which already includes the first checkpoint above. The next task-contract group exposed a second production omission: local schedule producers historically omitted the `owner` field, while durable definition list/change compared that missing field to an empty owner. Both producers now stamp the current owner explicitly. Definition list/change and legacy occurrence cancellation consistently treat a missing owner as unowned, while named users and administrators remain excluded from other owners' schedules. Tests cover local legacy history, reviewed retirement preserving cancelled occurrences, and refusal for a named administrator.

The following files pass after focused reruns: `test_schedules` (9), `test_session_tree_api` (14), `test_agent_action_trace_api` (4), `test_control_surface` (21), `test_api` (54), `test_rbac` (32), `test_api_robustness` (4), `test_ui_goal_detail_a11y` (3), `test_ui_client_initialization_order` (4), and `test_voice_module` (2): **147 cases**. The shared external answer controller is deferred, so its initialization test now verifies the actual defer/synchronous-helper ordering contract rather than assuming it remains inline.

Additional changed-service qualification passes all **29 work-operations cases**, **6 durable schedule definition cases**, **3 Copilot schedule repair cases**, and the targeted **pilot admission/reviewed restart case**. The work-operations fixture uses the real gist preview, audit and receipt services with only the GitHub/token boundary mocked; an exact acknowledged retry performs no second upload. Archive comparisons allow separate preparation timestamps while verifying the original v1 content checksum. The pilot fixture supplies the current reviewed version, owner and context to the native restart service.

Total for this follow-up: **186 distinct passing cases** (some overlap the first checkpoint; totals are not additive across checkpoints). Matching local audit artifacts: `task-contract-regressions.xml`, `task-contract-rerun.xml`, `schedule-contract-regressions.xml`, `work-ops-contract-rerun.xml`, and `task-export-pilot-contracts.xml`. Earlier intermediate failures are retained in those logs and resolved by the later targeted reports. Ruff, AST and whitespace checks pass. No live GitHub or model-provider call occurred.
