# Optional parser and analytics qualification

Qualification date: 2026-10-01. Frozen starting source: `de699529b01a87f371ebcfe96c57bd3c022d91f9`. Platform: Windows AMD64, CPython 3.12.12.

## Dependency provenance

The optional requirements are `maverick-knowledge[parsers]` (`python-docx>=1.1`, `pypdf>=6.14.2`) and the core/dashboard `duckdb` extra (`duckdb>=0.9`). `requirements/ci.txt` constrains the existing dependencies but does not pin python-docx, DuckDB or ReportLab.

Only official PyPI binary wheels were installed, into the private task-local `.deps-optional-parsers-de69952` target. Pip used `--isolated --only-binary=:all: --no-deps --index-url https://pypi.org/simple --constraint requirements/ci.txt --target ... --report ...`. The shared `.venv-audit` dependency set was unchanged. Import probes confirmed the two product extras loaded from the private target.

| Distribution | Qualified version | Wheel |
| --- | --- | --- |
| python-docx | 1.2.0 | `python_docx-1.2.0-py3-none-any.whl` |
| DuckDB | 1.5.6 | `duckdb-1.5.6-cp312-cp312-win_amd64.whl` |
| ReportLab, synthetic PDF test fixture only | 5.0.1 | `reportlab-5.0.1-py3-none-any.whl` |

Their required dependencies were already present at the CI-constrained versions: lxml 6.1.3, typing-extensions 4.16.0, Pillow 12.3.0 and charset-normalizer 3.5.1. Existing pypdf 6.18.1 was reused. No transitive dependency upgrades were needed. [The machine-readable record](optional-parser-qualification.json) contains exact official wheel URLs and SHA-256 hashes.

## Executed coverage

Each posture now has **176 unique executable cases passing and 10 environment-required PostgreSQL cases skipped**. This is the resolved result of the broad cohort and affected-file reruns, not a claim that an initial failing run was green.

| Run | Ordinary | Secure |
| --- | --- | --- |
| Broad cohort after the SQL repair and encryption-aware DOCX fixture correction | 173 passed, two outdated analytics fixtures failed, 11 skipped | Same |
| Complete analytics file after fixture corrections | 12 passed | 12 passed |
| Formerly skipped native PDF/image-page case, after private ReportLab install | One passed | One passed |
| Resolved unique cases across these runs | 176 passed, 10 PostgreSQL skips | 176 passed, 10 PostgreSQL skips |

The selected cohort comprises:

```text
packages/maverick-knowledge/tests
packages/maverick-core/tests/test_attachment_companions.py
packages/maverick-core/tests/test_duckdb_analytics.py
packages/maverick-core/tests/test_cli_ops_kernel.py
packages/maverick-dashboard/tests/test_attachment_submission.py
packages/maverick-dashboard/tests/test_insight_ops_api.py
```

Tests used `python -m pytest -q -rs` with JUnit output, matching-source `PYTHONPATH` for all eight packages, unique `.t` basetemp/session homes, provider/credential variables removed, voice model fetching disabled and an external-socket denial hook. Windows loopback sockets were allowed for asyncio's internal self-pipe. An initial overly strict test-only hook blocked that self-pipe; correcting the harness resolved those setup failures without changing product code.

Ordinary mode set both `MAVERICK_TEST_SECURE_DEFAULT=0` and `MAVERICK_SECURE_DEFAULT=0`. Secure mode set both to `1`, with a synthetic test-only audit key; the core fixture continued to supply per-test synthetic keys. No live provider, production or external database calls were made. The PostgreSQL/pgvector tests remain skipped in this cohort because its credential-scrubbed environment intentionally has no dedicated service DSN; their separate qualification belongs to the PostgreSQL cohort.

The real DOCX tests cover body/table order, source hash and location coverage, original bytes, rejected Word fields and companion generation. The secure fixture now reads plaintext through the existing attachment reader and separately verifies that stored original ciphertext is unchanged. Encryption remains enabled. The real PDF test covers text plus an image-only page and verifies explicit omitted coverage without claiming OCR confidence. DuckDB checks include query bounds, single-statement/read-only enforcement, disabled file access, timeout handling and ownership.

Ruff, Python 3.10 syntax review and whitespace checks passed for the modified files. This environment does not qualify every supported interpreter or platform version.

## LW-095: raw SQL content-owner boundary

Enabling the previously absent DuckDB extra exposed a remaining route in the approved employee-content privacy migration: `/api/v1/analytics/query` required global-administrator permission but constructed `_analytics(None)`, loading every employee's task title and episodes. The picker route already used the central owner filter.

Commit `31cedbfeee0762d503bcd5858c9710bb6d6b2965` captures `goal_owner_filter(request)` before thread dispatch and supplies it to the SQL analytics view. Global-administrator permission remains required; that capability does not grant ownership of another employee's private content. Named administrators retain their own rows, while unidentified installation access is limited to unowned work. This repair belongs to original **LW-095**, not an additional PR2530 finding.

The exact three new regressions were copied into an isolated prepatch checkout at `de699529b01a87f371ebcfe96c57bd3c022d91f9`. Only the test file differed. The product module's working-tree blob and committed blob both remained `c3aa5adbe29b437422365d40e02fb0b06e83e637`.

| Caller | Prepatch result | Fixed result |
| --- | --- | --- |
| Unidentified local installation access | Both synthetic employee titles and spending rows leaked | No employee rows |
| Named global administrator Ada | Ada and Bob rows returned | Ada row retained; Bob absent |
| Named global administrator Bob | Ada and Bob rows returned | Bob row retained; Ada absent |

**Before: three of three regressions failed. After: the same three passed in ordinary and secure modes.** The complete analytics file then passed 12 cases in each posture, retaining its operator-denial and read-only-query assertions.

Adjacent inspection found one dashboard `WorldAnalytics` construction, now reached through the scoped helper. Task health and recorded-step reads retain their central goal-access checks. Governance metadata permissions were not broadened or conflated with content ownership.
