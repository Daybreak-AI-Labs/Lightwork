# Lightwork client reliability audit — September 13, 2026

This review found and repaired client-impacting defects in file handling,
connector responses, Shoring records and measurements, Kubernetes execution,
browser forms, installation instructions, and CI coverage. The original
checkout was preserved; repairs were made in a separate checkout and published
through [PR #2510](https://github.com/Daybreak-AI-Labs/Lightwork/pull/2510).

The evidence supports a substantially stronger release candidate. It does
**not** establish that every feature, external integration, customer workload,
or deployment environment works. The unresolved items below are part of the
result, not implied passes. In particular, a one-time license with separately
priced support still needs a qualified installation and integration path.

## Source and method

- Baseline: `e0140f85aa3f67c4dd034f542d61d5bbbcb28baa`, fetched from `main`.
- Main repair commit: `e4d67d2`; follow-up parser, UI, CI and documentation
  commit: `8ac6356`; final runtime repair: `301c237e43ba4cab78000cfefac728c92d6b576f`.
- Local platform: Windows, Python 3.12.10, Node 24.13.0. All eight editable
  package imports were checked against the intended checkout. A separate
  environment installed real wheels with no editable source imports.
- Existing tests were run before changes. New regression cases reproduced
  failures before repairs. Validation included the ordinary and secure-default
  configurations, serial concurrency tests, browser journeys, wheel installation,
  isolated document parsing, a synthetic encrypted backup/restore drill, and
  GitHub's Linux and Windows workflows.
- External responses were synthetic unless explicitly identified otherwise.
  No customer account, production tenant, provider purchase, or live business
  transaction was used. GitHub and package downloads are infrastructure access,
  not customer integration acceptance.

## Findings and repairs

Impact is a practical client-impact classification, not a CVSS score. A row can
contain several related failure cases; test case totals are not bug counts.

| ID | Impact and trigger | Previous behavior | Repaired behavior and evidence |
| --- | --- | --- | --- |
| R01 | High: large REST/GraphQL results | Responses could be cut at 4,000 characters in the middle of JSON; a caller could mistake a partial response for complete data. | Bounded responses remain parseable JSON and explicitly identify incomplete bodies. Escaped text and large non-ASCII headers are covered; headers cannot consume the envelope indefinitely. |
| R02 | High: redirects, accepted jobs, revision-sensitive writes | A redirect or HTTP 202 could look like completed work, while callers lacked structured revision/status information. | Redirects are refused; 202 remains visibly pending. Opt-in metadata exposes status, body and an allowlist of revision/retry headers. Cookies and arbitrary headers are excluded. |
| R03 | High: malformed connector arguments or restricted GraphQL host | Invalid write arguments could reach request construction; GraphQL did not apply the connector capability host scope consistently. | Bad body/parameter/variable shapes fail before network activity. GraphQL observes the capability host restriction. Mutations are not automatically retried or redirected. |
| R04 | High: failed CSV/XLSX writes | A serialization or replacement failure could damage the existing file. | Complete output is staged before replacement. Failure-injection tests verify original bytes survive and owned temporary files are cleaned up, including Windows read-only cleanup. |
| R05 | Medium: spreadsheet input and bounded inspection | Row strings could become individual character cells; invalid limits could raise outside the tool; a UTF-8 BOM altered the first column name; a capped CSV count appeared exact. | Inputs require row arrays and an integer limit from 1 to 10,000. BOM handling is normalized, workbook handles close, and capped counts explicitly say “at least.” |
| R06 | High: editing an `.xlsm` workbook | A workbook save could discard macros. | Macro-enabled mutations are refused before loading/writing. Reading remains available. This is a fidelity safeguard, not a claim that macro-preserving editing is implemented. |
| R07 | High: PDFs in a customer workspace or malformed/large PDFs | The registered reader resolved relative files against the server working directory; invalid page syntax could select all pages; malformed arguments/parser errors escaped; local files lacked the remote byte limit. | The tool receives the assigned sandbox workspace. Invalid requests return tool errors. Local and remote input share a 100 MiB ceiling; output has a validated bound. Parser isolation is preserved. |
| R08 | Medium: blank PDFs or table extraction | Page labels could make an empty document appear to contain useful text; numeric zero and `False` disappeared from tables; cell delimiters broke structure; missing table support silently fell back to plain text. | Empty selected pages request OCR. Tables preserve values and escape cell boundaries. An explicit table request without `pdfplumber` returns an actionable dependency error. |
| R09 | High: concurrent or capacity-bound Shoring edits | Concurrent writers could lose changes; adding a 501st function could silently displace stored work; corrupt books could be treated as empty during mutation. | Read/modify/write uses a shared process lock. Capacity is explicit; existing records remain editable at capacity. Strict mutation reads refuse damaged books without overwriting them. |
| R10 | High: nested damage in a Shoring record | A later edit could silently discard invalid/excess SLAs or normalize corrupted status/timestamp fields. | Strict reads reject these records before saving. Four real-file regression variants first failed, then passed with original bytes preserved. Recovery-oriented permissive reads remain available explicitly. |
| R11 | High: Shoring outcome/SLA reporting | Other departments could displace relevant work from a global bounded query; an incomplete sample could receive an SLA grade; empty data could look like a breach; a zero exception target could not be represented usefully. | Department filtering precedes the query bound. More than 2,000 sampled goals is disclosed as incomplete and ungraded. Empty work is unmeasured; zero exception targets work. Store failures are visible rather than becoming empty measurements. |
| R12 | Medium: opening Shoring with slow or unavailable storage | Synchronous report reads ran on the HTTP event loop; corrupted data could produce an unhelpful server failure. | Reads run in the thread pool. Unavailable data produces a bounded 503 recovery message. The repaired route was removed from the existing concurrency exception list. |
| R13 | High: starting a Kubernetes sandbox | Default Docker-style `4g` memory was invalid for Kubernetes; JSON merge overrides replaced generated container image/command fields; non-root workspace permissions were incomplete. | Memory is translated to valid Kubernetes units, strategic merge preserves image/command, and the workspace receives group ownership. Actual `kubectl` with synthetic discovery validated manifest generation; no real cluster was used. |
| R14 | High: Windows Kubernetes transfer or failed export | Drive-letter colons were misinterpreted by `kubectl cp`; an export failure could return successful execution. | Copy operands are unambiguous and use the intended working directory. Export failure after a successful command returns exit 74, preserving command output and the error. Existing command failures remain failures. |
| R15 | High: Kubernetes network policy changes | A namespace-wide deny policy could coexist with an additive allow policy, and prior policy validation was reused. | Policy is checked before every execution; namespaces with egress allow rules are refused for no-network execution. Enforcement by the cluster CNI still requires live qualification. |
| R16 | Medium: browser form validation | 84 HTML patterns in 19 templates were invalid under current Unicode `v` regular-expression semantics, weakening browser validation. | Patterns were corrected. A parser-backed JavaScript gate now compiles all 203 shipped patterns and fails on invalid syntax. |
| R17 | High: installing the platform from documentation | Instructions installed a subset of packages while describing the whole platform. Optional document parsers and a Python 3.10-only SMS transitive dependency were absent from the reviewed dependency closure. | README/getting-started use the eight-package release manifest, a virtual environment, a reviewed revision, explicit runtime/document extras, and POSIX/PowerShell instructions. Four parser pins and the Python 3.10 `async-timeout` pin were added; the installed closure is checked in the full matrix. |
| R18 | Medium: test failures masking real regressions | Exhaustible clock fixtures, an underspecified research-job race, cold setup consuming a timeout fixture's budget, Windows path spelling, and browser interpreter selection caused misleading failures. | Fixtures now test the intended timing/race semantics deterministically. A separate case covers budget consumed during setup. Windows path and interpreter handling are corrected without weakening production guards. |
| R19 | Medium: CI coverage that appeared broader than it was | Seven setup/recovery browser journeys were skipped by the general harness; parser/SMS tests lacked their dependencies; PR checks with success status could omit newer-interpreter or secure-posture test bodies. | CI explicitly runs the seven setup journeys, installs the reviewed parser/SMS extras, and exposes manual dispatch for the full interpreter/posture matrix. Actual executions are distinguished from skipped job bodies below. |
| R20 | Medium: checked-in benchmark evidence | Source changes made the signed governance/overhead manifests stale, correctly failing CI. | Both benchmarks were measured again from separate clean checkouts of the final runtime commit. Manifests, reports and public verification keys were regenerated together; gates were not bypassed and signatures/digests were not hand-edited. |

The main executable regressions are in
[`test_client_reliability.py`](../../packages/maverick-core/tests/test_client_reliability.py).
Related coverage lives in the existing PDF, Shoring, Kubernetes, agent-loop,
research-job, dashboard route, browser, and JavaScript test files changed by the PR.

## Validation record

Counts are per execution, with overlapping coverage. They must not be added
together and presented as a number of unique tests or defects.

| Validation | Result | Scope and qualification |
| --- | --- | --- |
| Baseline collection | 29,254 collected; no collection errors | Original source, local Windows environment. |
| Baseline full Python run | 28,906 passed; 3 failed; 343 skipped; 3 expected failures | Two Kubernetes transfer regressions and the Windows async-route audit key failed. Additional isolated CI-fixture reproduction had 3 failures. |
| Initial dedicated regression pack against baseline | 39 failed; 1 skipped | Forty parameterized client cases; the skip was an optional dependency. This was evidence of defects beyond the pre-existing suite. |
| Full Python after primary repairs | 28,954 passed; 340 skipped; 3 expected failures | Normal posture at `e4d67d2`, 26m 06s. Zero failures. |
| Full Python with secure defaults | 28,954 passed; 340 skipped; 3 expected failures | `MAVERICK_TEST_SECURE_DEFAULT=1`, same primary repair source, 27m 05s. Zero failures. |
| Serial heavy concurrency | 7 passed | Queue/control-plane soak and discovery estate runner, 27.18s. |
| Follow-up affected paths, normal and secure | 162 passed; 4 skipped in each run | Additional parser/channel dependencies installed. Remaining skips: Slack SDK, Matrix SDK, two Telegram SDK cases. |
| Final PDF/REST follow-up | 50 passed | Includes explicit refusal when table extraction is requested without its dependency. |
| Final Shoring/core/route follow-up, normal and secure | 86 passed in each run | Includes all 48 dedicated client regression cases and the four nested record-damage variants at `301c237`. |
| Dashboard JavaScript | 745 passed | Final parser dependency lock and all 203 form-pattern compilations. |
| Other JavaScript suites | Desktop 19; mobile 40; TypeScript SDK 15 passed | These are source tests, not installed application acceptance. |
| Browser journeys | 60 passed in general run; 7 passed in explicit setup run | 67 distinct journeys. Real local application/storage with synthetic external services; desktop/mobile viewports, no real phones. |
| Release wheels | Eight distributions built and installed without editables; cohort verification and `pip check` passed | Actual package imports and 2,031 packaged specialist templates checked. The catalog contains 54 named suites plus profiles without a suite. Template count is not evidence of 2,031 production-qualified workers. |
| Wheel runtime | Real PDF parsing in an isolated child, XLSX round-trip, five dashboard routes passed | Preserved leading-zero order ID and numeric quantity. Health checks configuration presence only; no live provider request was performed. |
| Dependency closure | 61 distributions; zero unexplained version/constraint errors | Production wheel environment with `release-runtime,evidence-analysis`. Optional extras remain deployment-specific. |
| Encrypted backup/restore | Passed synthetic local restore and fresh-process retained-key verification | Original synthetic key was removed before the retained-copy check. No real workspace or external data store was modified. |
| Static checks | Ruff and Vulture passed | Required rules remain enabled. Existing contract and posture gates are retained. |
| Signed offline governance benchmarks | Fresh measurement and tracked-artifact verification passed | Clean final source, pinned seeds, real control paths, stubbed model boundary. Co-located signing proves run integrity; it is not independent certification or customer performance evidence. |

The first published repair also passed Linux Docker, PostgreSQL, audit, red-team,
release-cohort, evaluation smoke, Python 3.10 shards and Windows containment
checks in [CI run 34736737615](https://github.com/Daybreak-AI-Labs/Lightwork/actions/runs/34736737615).
That run's two stale benchmark checks failed; R20 addresses their cause. Its
3.11/3.12 and secure-posture jobs skipped their main bodies on a pull request and
are **not** claimed as actual full-suite passes.

The [Windows package acceptance run 34736737626](https://github.com/Daybreak-AI-Labs/Lightwork/actions/runs/34736737626)
passed install, repair, upgrade, transaction rollback, completed-upgrade rollback,
signature-tamper rejection and publisher-transition acceptance using disposable
test identities. Customer signing was skipped. This proves the test-package path
on a GitHub Windows runner, not a production-signed release installed at a client.

The PR description records the final published revision and links the subsequent
PR and manually dispatched full-matrix executions. Check those execution links
for the final GitHub result; this local evidence table does not turn pending or
skipped remote jobs into passes.

## Remaining client readiness gaps

| State | Area | What remains and why it matters |
| --- | --- | --- |
| Unresolved engineering debt | Dashboard concurrency | The committed detector still lists 149 handlers with synchronous work inside zero-await async routes. These are concrete static findings, not 149 measured outages. Slow storage or concurrent requests can stall the event loop. The Shoring route was fixed; the remaining handlers need route-specific review and concurrency acceptance before claiming broad load readiness. |
| Known functional limit | Shoring measurements | Turnaround is declared but remains unmeasured. Sampling beyond 2,000 department goals is explicit and ungraded. Exception association is a department-level heuristic. A Book of Work is limited to 500 functions and 12 SLAs per function. These counters do not prove financial savings or autonomous service delivery. |
| Known functional limit | Kubernetes workspace transport | Copy transport does not mirror file deletions to the host. A failed export is now a failure, but normal pod cleanup can discard unexported output. Use a qualified bind-mounted backend when deletion propagation or stronger output recovery is required. |
| External qualification | Kubernetes containment | A real cluster, configured namespace, chosen image, and CNI packet-filtering test are still required. Reading NetworkPolicy objects is not a packet-level isolation test. |
| Known functional limit | Office documents and PDF OCR | Macro-enabled editing is refused. XLSX formula reads depend on cached values; the tool does not calculate formulas. Tables need `pdfplumber`; blank/scanned PDFs need the configured OCR path. Parser availability and representative customer documents must be checked for the sold configuration. |
| External qualification | Customer integrations and AI providers | Each advertised integration needs real credentials, permission scopes, tenant configuration, endpoint behavior, and approved read/write acceptance. Synthetic response tests do not validate provider availability, OAuth approval, rate limits, or a customer's system of record. |
| External qualification | Customer installer and updates | Windows disposable-package acceptance passed. The actual signed customer artifact, publisher/channel configuration, customer OS/security software, proxy, upgrades and rollback still need release acceptance. Native Mac/Linux packages and physical mobile devices were not qualified here. |
| Unverified optional integrations | Additional SDKs/backends | Local skips include PostgreSQL/native/Linux-only and optional SDK tests; GitHub covers several of these separately. Do not equate a local skip with a pass or equate one extra configuration with every supported optional dependency combination. |
| External qualification | Backup, throughput and business outcomes | The local encrypted recovery drill passed. Customer key custody, external stores, operational retention, production load and recovery time remain environment-specific. No measured customer revenue, savings, accuracy or hiring reduction was established by this audit. |
| Explicitly unsupported | Firecracker scaffold | The local backend scaffold is not a qualified working deployment target. Its documented unsupported state is not resolved by tests of other sandbox backends. |

For a client handoff, select the actual supported deployment and purchased
features, run acceptance with that client's configuration and representative
documents, and record the result. The broad platform inventory and offline test
suite should not be substituted for that acceptance.

## Reproduction and evidence

Use the manifest-driven setup in [getting started](../getting-started.md) and
the reviewed test dependencies in `.github/actions/test-shard/action.yml`.
Confirm that imports resolve into the checkout under review before testing.

```powershell
python -m ruff check .
python -m vulture
python -m pytest -q -n 4 --dist loadfile -m 'not heavy_concurrency' --timeout=180 --timeout-method=thread
$env:MAVERICK_TEST_SECURE_DEFAULT = '1'
python -m pytest -q -n 4 --dist loadfile -m 'not heavy_concurrency' --timeout=180 --timeout-method=thread
Remove-Item Env:MAVERICK_TEST_SECURE_DEFAULT
python -m pytest -q -m heavy_concurrency --timeout=600 --timeout-method=thread
python scripts/install_release_cohort.py --verify-only
python scripts/verify_constraint_closure.py
python benchmarks/eval_governance_frontier.py --verify-tracked-artifacts
python benchmarks/eval_harness_overhead.py --verify-tracked-artifacts
```

The CI workflow uses bounded Linux shards and keeps the heavy tests serial.
The UI workflow runs the general browser journeys and the seven explicitly
enabled setup/recovery cases. It also runs the JavaScript tests.

Local raw logs, XML reports and standalone probes are retained in the audit
workspace's `work/client-audit-evidence` directory. The companion
[`client-reliability-2026-09-13.json`](evidence/client-reliability-2026-09-13.json)
records selected file hashes, source revisions and CI links. Hashes identify
the captured evidence; they do not make local tests independently certified.
Executable regressions and the regenerated benchmark artifacts are committed
with this report so the important claims can be checked from the repository.
