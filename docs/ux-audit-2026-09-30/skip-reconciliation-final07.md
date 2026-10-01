# Final07 skipped-test reconciliation

The complete ordinary Python 3.12 run at
`2a765aa3e93cc7775090f574f027bd855b33aef5` recorded **40,072 passed, one failed,
590 skipped and 22 warnings** in 3,281.79 seconds. Its source remained unchanged;
the transport-supervision failure remains a failure requiring investigation.
Supplementary passes below do not rewrite that result. The [identity ledger](skip-reconciliation-final07.json)
binds the current XML, log and source/result receipts by SHA-256.

All 558 entries from the [historical inventory](skip-reconciliation-7d38f76.md)
remain in the new 590-entry skip set. The 32 additions are precisely the new
`test_qdrant_text_compat` identities: the full-run environment lacks
`qdrant_client`. Every one passed under both postures in the
[current-client native Qdrant receipt](qdrant-current-client-qualification-e22ffbc.md),
with unchanged test blobs relative to final07. No additional gap is inferred
from those 32 skips.

| Distinct skipped entries | Historical 558 | Current 590 |
| --- | ---: | ---: |
| Executable identities with supplementary passes | 425 | 457 |
| Collection placeholders expanded by supplementary runs | 5 | 5 |
| Entries still requiring a prerequisite or intentionally inapplicable | 128 | 128 |

The original 424 recovered executable identities gain exactly one new recovery:
the real Qdrant tenant-isolation case, now exercised with verified offline ONNX
embeddings and the actual loopback server. The 32 new native compatibility cases
then produce 457 recovered executable identities in the current inventory.
All 457 have ordinary and secure-posture passing evidence. The five collection
placeholders expand to 27 distinct cases; those cases are separate from the
recovered-executable count.

The [363-case secure PostgreSQL run](postgresql-secure-gap-qualification.md)
covers **270 already-recovered original identities**, closing their posture gap;
the other 93 selected cases lie outside this skip inventory. It adds no new
unique recovery to the 425 or 457 totals. Qdrant's repeated synthetic/offline
and ordinary/secure executions likewise count once per identity. Raw referenced
Qdrant and PostgreSQL artifact hashes were reverified for this reconciliation.

The remaining 128 entries comprise 124 executable environment/provider gates,
one intentionally inapplicable Chroma parameter and three unexpanded SWE-bench
collection placeholders. The executable gates are 45 Windows symlink-privilege
cases, 44 cases requiring another operating system's semantics, 12 container
research/cleanup cases, seven document-engine container cases, 12 pgvector
extension cases requiring the absent compiler prerequisites, three paid-provider
cases, and one SpeechBrain model/sample case. Public SpeechBrain asset and
isolated-runtime preparation is underway; it is still an unexecuted case here.
The unavailable daemon, native-platform and provider boundaries remain explicit.

This is a reconciliation of source-bound receipts, not a complete integrated
pass on the review source `0d0755b0ba4389d49357a4f6354cd758ab145e4e`.
Historical parser-cohort provenance limitations remain unchanged. Fresh final
ordinary/secure/heavy and supported-interpreter runs remain separately tracked.
