# Client readiness follow-up — September 13, 2026

This follow-up addresses the code gaps left after PRs #2510 and #2506. It starts
from `12f869c292049a8b2e33d3438c95f8266078a244` in an isolated checkout and is
published through [PR #2512](https://github.com/Daybreak-AI-Labs/Lightwork/pull/2512).
The earlier audit remains a historical record of its own source and results.

## Repairs and their client effect

| Area | Changed behavior | Evidence and boundary |
| --- | --- | --- |
| Dashboard responsiveness | 149 blocking handlers run in FastAPI's thread pool. Direct callers retain the correct sync/async contract. Tenant and owner context survives slow storage. | Concurrent requests exercise real route handlers. The grandfathered zero-await route register is empty; its detector remains enforced. |
| Injection defenses | The three missed conversation/setup instruction families are covered by Shield and the independent core floor, including encoded variants. The local floor still runs when the optional SDK permits content. Profile thresholds resolve consistently. | Shield/core regressions, benign controls and per-rule deletion checks. No claim of universal injection resistance or qualification of a live agent-shield SDK. |
| Consent authority | Public/admin/native consent paths resolve the authoritative policy directly, including exact historical versions. Unrelated policy history cannot hide authority beyond a 500-row listing. | Real-store lookup tests and public choice/receipt round trips with more than 500 intervening versions. UI pagination remains bounded. |
| Account renewal | Credential and OAuth registration readers share the writer's process-safe lock through permission verification and decryption. Legitimate atomic rotation cannot trip the file-identity check mid-read. | Two coordinated race reproductions failed before the repair; 110 credential/OAuth/locking regressions passed afterward with five platform-dependent skips. Corruption refusal and single-flight renewal remain tested. |
| Shoring | Measurements aggregate all matching rows. Completion time is recorded on transition, retained through result edits, and cleared for a rerun. Legacy unknown completion times remain unknown after editing. Approval attribution comes from trusted goal context. | A 2,101-record SQLite case and PostgreSQL tenant tests. Unassigned approvals are shown separately; their free text does not assign them to a department. Migration 36 is appended to both ladders. |
| Kubernetes file delivery | Input snapshots, bounded validated exports, deletions/renames, concurrent-edit conflict detection and recovery copies replace blind copy-back. Failed delivery returns non-success. Failed exports retain the pod and recovery location. Empty initial workspaces skip unnecessary root-directory metadata restoration. | Real local files with synthetic kubectl transport, plus real subprocess archive-limit tests. Actual cluster/CNI acceptance is still required. See [recovery](../sandbox-kubernetes-recovery.md). |
| Business documents | Optional XLSM cell edits preserve untouched VBA/ZIP parts. Formula reads expose cached versus uncalculated state. Offline Calc recalculates formulas; OCR reads scanned pages. Spreadsheet read bounds come from actual coordinates. | Four actual native-engine CI tests passed: cross-sheet arithmetic/text formulas and repeat calculation, formula-error preservation, scanned invoice OCR, and synthetic VBA-byte preservation. These do not qualify arbitrary customer macros or complete Excel compatibility. |
| Customer packaging | Full release artifacts include document parser libraries and the standalone worker source, which must be a resource outside the frozen PYZ archive. The release runtime check imports the real parsers and checks that source. | Packaging regressions and the expanded runtime check; the Windows customer workflow tests the frozen executable. Native engines still require their configured executor image. See [document setup](../document-processing.md). |
| Pricing and receipts | Evidenced conservative Standard price ceilings cover the new model family. Promotional expiry is enforced for new billing lookups and retained in receipts. Older non-expiring receipt normalization stays unchanged. | Quote/budget tests plus durable outcome/provider settlement, retries and concurrency tests. Historical receipts remain readable after a rate expires. Rates are budget ceilings, not provider invoice amounts or Fast/priority prices. |
| AWS execution | Managed application execution is pinned to a separate Kubernetes worker namespace. Local/SSH overrides are refused. Terraform requires a digest-pinned worker image and scoped kubeconfig secret; IAM tagging permissions include the corresponding untag actions. | Configuration/credential refusal tests, Terraform validation and application image build. This is deployment preparation, not evidence of an applied or qualified production executor. See [executor setup](../../deploy/aws/EXECUTOR.md). |

## What validation established

The initial broad Windows run completed with **29,271 passed, 41 failed and
326 skipped**. It ran before the final corrections and must not be described as
a clean release run. Every failure was investigated. The main functional
regression was an exact receipt-schema check rejecting the new optional expiry
field. Other failures identified missing credential registration, deployment
extra parity, stale migration/route fixtures and missing mutation-corpus cases.
The first GitHub interpreter/posture run reproduced those failures. Its earlier
dependency-closure failures also led to three explicit PDF dependency pins.
The Windows package job also exposed a maintenance test that exhausted its
five-second timeout during cold PowerShell startup. Its fixture now starts
native parent/descendant test processes, requires the descendant's own readiness
signal, and checks bounded process termination through a Windows process handle.
The production maintenance deadlines are unchanged.
Another interpreter shard exposed an intermittent concurrent account-renewal
failure. The permission verifier correctly rejected a changed inode, but a
legitimate credential writer could replace that inode during an unlocked read.
Both sealed-store readers now coordinate with the existing writer lock without
relaxing the verifier. Coordinated tests cover credentials and app registrations.

Subsequent targeted runs passed, including 227 accounting/provider/route and
deployment regressions, 131 backup/security/API regressions, 109 Kubernetes/file
regressions, 594 Shield/core tests, and 58 packaging/document regressions. These
runs overlap and are not a combined test total. The serial heavy-concurrency
phase passed 7 tests with 8 environment-dependent skips. Control/data-plane e2e
and the 60-goal/four-worker soak passed using a stubbed model boundary.

Ruff, Vulture, contract/governance checks, migration fingerprints, dependency
constraint closure, Terraform validate/fmt and the TypeScript SDK tests were
also exercised. The actual native document CI job passed four tests; the live
PostgreSQL job passed 131 core cases and two vector-store cases. Both signed
offline benchmark artifacts are regenerated from a clean committed source
snapshot whenever the covered runtime changes; their verifiers are not bypassed.

Final full-matrix status belongs to the immutable CI runs linked from PR #2512.
The branch's manually dispatched CI run executes Python 3.10–3.12 and all three
production-security-posture slices. Pull-request placeholder jobs that skip
those slices are not acceptance evidence. The PR also carries browser, native,
client SDK and Windows package checks. Inspect their actual results before
promoting the candidate; the focused totals above do not replace them.

## Remaining environment acceptance

The repository fixes do not provision a Kubernetes cluster, apply Terraform,
populate Secrets Manager or validate a running customer tenant. Production
rollout now refuses missing executor configuration intentionally. Qualify the
cluster's packet isolation, denied cloud metadata access, credential separation,
timeouts and file recovery using the supplied procedures. Kubernetes policy
objects alone do not prove that a CNI enforces them.

The signed Windows release/install/update path with the real publisher identity,
customer account onboarding and third-party integrations still need their
authorized environments. A synthetic CI installer or provider response is not
that acceptance. Customer workbooks also need representative compatibility
checks. The local Firecracker scaffold remains unsupported. This follow-up
does not establish that the entire platform is free of client-impacting bugs.
