# Exact-case skip reconciliation

Inventory source: `7d38f76ccc7c8b1752eaeb44d65dccfd0646d4d5`, recorded 2026-10-01. This is a reconciliation of existing evidence, **not a new full-suite pass**. The [machine-readable inventory](skip-reconciliation-7d38f76.json) preserves all 558 original skipped identities, reasons, dispositions, supporting runs, source-reference quality, test-file Git blobs and raw JUnit hashes.

The complete full03 ordinary run, on `2c1e8a888eee011a2851836079b57c9929b8d745`, recorded **39,987 passed, 17 failed and 558 skipped**. Its skips comprise 550 executable testcase identities and eight module-collection placeholders. Matching uses the exact JUnit classname/name pair, including class and parameter IDs.

| Disposition of the original 558 entries | Entries |
| --- | ---: |
| Exact executable identity passed in supplementary qualification | 421 |
| Exact negative-path identity passed with simulated optional-dependency absence | 3 |
| Collection placeholder expanded into separately executed cases | 5 |
| Windows symbolic-link privilege unavailable | 45 |
| Different operating-system semantics required | 44 |
| Opt-in container runtime and image required | 12 |
| Document-engine container image required | 7 |
| pgvector extension build toolchain unavailable | 12 |
| Live paid provider authorization required | 3 |
| Real Qdrant server and FastEmbed model required | 1 |
| Local speaker model and named samples required | 1 |
| Intentionally inapplicable Chroma parameter of a Qdrant-specific test | 1 |
| SWE-bench container-harness collection placeholders | 3 |
| **Total original entries** | **558** |

Thus 424 original executable identities have passing evidence at their recorded source boundaries. Five collection placeholders expand to **27 distinct cases**, recorded separately; those 27 are not added to the recovered-skip count. The remaining 129 original entries comprise 125 executable environment/provider gates, one intentionally inapplicable parameter and three collection placeholders.

## Evidence without overlapping totals

The 421 regular supplementary recoveries are the union of runtime/native/SDK/parser/vector/database receipts. A disjoint accounting assigns 113 to optional runtimes/native parity, 12 to installed SDKs and CPU Torch, 12 to document parsers/DuckDB, seven to native local vectors, seven to the latest MariaDB/PostgreSQL query cohort, and 270 to the remaining ordinary PostgreSQL cohort. The PostgreSQL receipt alone matches 274 original skipped identities; four overlap the later seven-case query cohort and are counted once.

The five collection expansions are Z3 capability proofs (four cases), DuckDB analytics (12), computed metrics (six), pandas queries (three), and Telegram target identity (two). The other three collection placeholders remain the official SWE-bench/Docker harness modules. A collection placeholder is never represented as one passing executable test.

Of the 424 recovered executable identities, **154 have both ordinary and both-flags secure passing evidence; 270 have ordinary PostgreSQL evidence only** at this snapshot. Earlier PostgreSQL `secure_core_fixture` runs did not set both secure flags before collection and are not promoted to that stronger claim. The exact 270 IDs and 29 complete files were handed to the database qualification worker in `audit/pg-secure-gap-7d38f76{,-targets,-files}.json`, with the original `postgres-de699-targets.json` selection and `run-postgres-qualification.py` helper. Any later native rerun is separate evidence.

For 412 recovered identities, at least one supporting immutable-source reference has a test-file Git blob identical to the inventory source. The remaining 12 parser identities refer to the final committed parser checkpoint, `b565bb74`, because the older parser receipt did not attest an immutable head for every pre-commit broad/focused run. That provenance limit is retained explicitly. Even an identical test-file blob does not prove that every transitive product module or environment remained unchanged, and none of these results is presented as a fresh full run on `7d38f76`.

Native-client, native-platform and installer receipts were also checked. Their passing Python cases do not recover additional identities from these 558 skips. Node/JVM/mobile source checks and unsigned local builds have separate identities and cannot clear missing OS, device, signing or release-installation qualifications.

## Newly executed small local follow-ups

On unchanged `7d38f76`, the complete `test_telegram_target.py` file passed **two cases in ordinary mode and two with both secure flags**. Its real installed Telegram SDK uses existing synthetic updates and handlers; no Telegram connection occurs. This closes the one feasible local collection omission identified by the inventory.

The two existing SMS/WhatsApp import-error cases and the PostgreSQL missing-extra case also passed **three cases in each posture**. Fresh subprocesses filtered only `twilio` and `psycopg` from import finders before collection, verified that `find_spec` returned `None` and actual imports raised `ModuleNotFoundError`, and then executed the original assertions. This is **simulated package absence**, not actual uninstallation or native provider integration. No installed package, product source or test assertion changed.

Both follow-ups reused the private SDK environment and existing external-network denial hook, isolated HOME/USERPROFILE/TEMP, removed inherited credentials, disabled model fetching, and ran serially. Secure invocations set both `MAVERICK_SECURE_DEFAULT=1` and `MAVERICK_TEST_SECURE_DEFAULT=1`. The receipt includes exact commands, source hashes, harness code and results. The network guard was unchanged.

## Remaining gates and the incomplete full04 run

The 45 symbolic-link cases require a Windows account with that filesystem privilege; the 44 OS cases explicitly exercise POSIX modes/UIDs/process groups, Linux `/proc` or fork behavior, names Windows cannot create, or macOS APIs. They were not forced through another platform's semantics. The 12 pgvector cases remain blocked by the previously documented absence of a C compiler, `nmake`, Visual Studio and Windows SDK; PostgreSQL headers alone do not supply that build toolchain.

Docker CLI availability is not a running container engine. Read-only inspection found no Docker Desktop/backend/dockerd process or standard local engine pipe, and no Qdrant process. No daemon was started and no images were pulled. The 19 executable container tests require their explicitly selected research/cleanup/document images; the three SWE-bench module gates also require the official harness. The Qdrant case additionally requires its actual server and FastEmbed model, and speaker verification requires its named local model/sample fixture. The three live-provider cases require explicitly authorized paid model calls. These are precise unrun gates, not passing checks or permanent claims of infeasibility.

The full04 ordinary artifact on `500fc87363a492def69722ef19d2cc518d171286` contains only **12,170 passed, one failed, one error and 133 skipped** before the run ended early. All 133 skipped identities are present in the full03 inventory: 97 have supplementary executable passes, five collection placeholders expand as above, and 31 remain gates (19 symbolic-link, five container, three SWE-bench collection, two OS and two pgvector). The other 425 full03 skipped identities were not recorded in that partial run. The active full04 secure run is deliberately excluded from this snapshot. Supplementary evidence does not convert the incomplete full04 run into a successful full suite.
