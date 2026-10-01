# Qdrant current-client repair and local qualification

The native qualification uncovered and repaired an actual adapter incompatibility:
Qdrant client 1.19.1 removed the text helpers the adapter called. At source
`e22ffbcca2af43887ed4c142b2633fd86a51471c`, the four complete native files pass
**74 tests with one intentional skip in each posture**. The unchanged real-server
tenant-isolation case also passes in each posture, first with synthetic embeddings
and then with **actual offline FastEmbed/ONNX CPU inference**. These are
**75 distinct passing test IDs**, not 152 distinct tests; 152 is the total passing
case executions across the six final runs.

The [evidence record](qdrant-current-client-qualification-e22ffbc.json) contains
every case ID/result, exact source and command, raw receipt hashes, helper hashes,
source-file Git blobs, server provenance, and model-asset hashes. This is a
supplement to the integrated suite, not a claim that a later full-suite run passed.

## Failure, repair, and regression coverage

On `500fc87363a492def69722ef19d2cc518d171286`, the unchanged complete
`test_qdrant_isolation_live.py` failed with
`AttributeError: 'QdrantClient' object has no attribute 'add'` against an actual
private Qdrant 1.19.1 server. The failure occurred before encoding. Its original
receipt is retained as `audit/qdrant-server-500fc87-ordinary.*`.

The adapter retains older clients' existing text-helper path. Current clients use
FastEmbed passage/query encoding and native point operations. Existing collections
must have a unique supported named-vector model or an explicit matching model;
recorded model, field, dimension, and distance must agree. Only a new collection
without prior schema may use the SDK default, and its full identity is persisted.
No application default model ID or deployment model/key configuration changed.

The first repair's native run exposed three real Windows reset failures: clearing
by deleting/recreating the local collection left earlier points visible. The
current path deletes points with `wait=True`, preserving schema and model identity.
The intermediate result, **54 passed, 3 failed, 1 skipped**, remains in
`audit/optional-vectors-ec8e9e6-ordinary.*`. It is not hidden in the final totals.

The 32 new compatibility cases and additional legacy-client regression cover:

- Existing/new collection identity, SDK-default changes, reopen/reset behavior,
  ambiguous or mismatched schemas, and rejection before mutation.
- Exact tenant/logical-ID isolation, metadata preservation, invalid vectors,
  preparation failures preserving existing points, and uncertain writes without
  automatic resend.
- Invocation checks around model initialization, each yielded vector, bounded
  16-document batches, and native calls; cancellation and expired budgets stop
  subsequent effects. Local encoding does not invent hosted-provider charges.
- Atomic publication of validated encoder/model identity, and client cleanup
  after rejected legacy initialization while the exception is still retained.

## Exact final results

All files below are complete files under `packages/maverick-core/tests/`.

| File / run | Ordinary | Both secure-default flags |
| --- | ---: | ---: |
| `test_recall_native_adapters.py` | 5 passed, 1 skipped | 5 passed, 1 skipped |
| `test_q2_2026_batch3.py` | 16 passed | 16 passed |
| `test_qdrant_store.py` | 21 passed | 21 passed |
| `test_qdrant_text_compat.py` | 32 passed | 32 passed |
| Four-file total | **74 passed, 1 skipped; 8.65 s** | **74 passed, 1 skipped; 6.17 s** |
| Real server, synthetic embeddings | 1 passed; 5.84 s | 1 passed; 5.89 s |
| Real server, actual offline ONNX | **1 passed; 5.91 s** | **1 passed; 6.03 s** |

Ordinary runners set both `MAVERICK_SECURE_DEFAULT` and
`MAVERICK_TEST_SECURE_DEFAULT` to zero; secure runners set both to one. The existing
native recall fixture explicitly disables encryption at rest. These results do
not qualify encrypted native-vector files. The sole skip is the Chroma parameter
of the Qdrant-specific exact-filter test; no missing-dependency skip remains in
these four files.

The real-server case is
`test_qdrant_isolation_live.py::test_tenant_scoped_add_query_delete`. Its Git blob
is unchanged from the failing `500fc873` source. It checks exact document/ID
results for two tenants sharing a logical ID and deletion without touching the
other tenant. Each actual offline run records **two passage vectors and four
query vectors** through the real encoder. Repeating that same case under two
encoders and two postures adds one unique passing test ID, and recovers the
previous no-server skip. The four-file native runs use synthetic embeddings.

## Dependency, server, and offline model boundary

Qualification used Python **3.12.12**, Qdrant client/server **1.19.1**, FastEmbed
**0.8.1**, ONNX Runtime **1.30.0**, NumPy **2.2.6**, and Chroma **1.5.9**. Four
additional exact-version official PyPI wheels supplied FastEmbed and its missing
dependencies to the private vector environment. Their URLs/hashes and installation
receipt hashes are recorded; supported dependency ranges were not changed.

The [official Qdrant release](https://github.com/qdrant/qdrant/releases/tag/v1.19.1)
Windows archive is 29,671,153 bytes and has published SHA-256
`9b6f69bd85f6abed4bc13f943099f55c6ffd55f5dd90388635320d8fbb569eb0`.
The harness verifies the archive and binds the launched executable to the exact
archive member. The executable SHA-256 is
`b5354e3c8f9d13d92294f38d4988d4ddedc04acaa2ce51f0a2d65b30e2d4cd69`.

Each server used fresh task-owned storage, a synthetic API key, one worker, and an
ephemeral `127.0.0.1` HTTP port. gRPC, clustering, telemetry, CORS, and remote
snapshot recovery were disabled. The runner stripped account/provider/proxy
variables and used proxy-free readiness checks. Each receipt identifies the
created process and reports `server_stopped: true` after terminating and waiting
for that process. The recorded Windows exit code 1 is the deliberate termination
result, not a pytest failure. HTTP-key and JUnit `record_property` warnings remain
visible; these tests do not qualify production TLS or authentication integration.

The approved model assets come from
[Qdrant's pinned model revision](https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q/tree/aa8f8b060edb00e03bfdd08813a2949946c8ba55).
The six-file set is **67,180,063 bytes**: a quantized ONNX model, four configuration
or tokenizer files, and an MIT-declaring README. The ONNX file matches its
published LFS SHA-256; small files match canonical Git-blob hashes. All six raw
SHA-256 values were independently rechecked against the manifest during this
receipt review.

The test-only cache binding retains real FastEmbed/ONNX and tokenizer execution,
sets the verified local path, `local_files_only=True`, one CPU thread, and
`CPUExecutionProvider`. Unexpected Hub/GCS download fallback fails immediately.
Assets were prefetched explicitly; runtime did not use hosted inference or
automatic downloads. This model is local qualification data for the SDK default,
not a deployment setting.

The Python audit guard rejects external DNS/socket destinations, including UDP
send operations. **It is not an OS firewall or native-process network sandbox.**
The native server configuration and offline encoder path provide separate
boundaries. Older-client mocks remain covered; no claim is made for every
historical SDK, server version, distributed cluster, or custom embedding model.

## Source and static checks

The seven repair commits are `ec8e9e6`, `4a72a91`, `2f38d02`, `4f287dc`,
`759e7bb`, `3e54424`, and `e22ffbc`; full SHAs appear in the evidence JSON. The
final source checkout was clean. Independent Ruff, Python 3.10 AST parsing, and
`git diff --check c906e07..e22ffbc` passed for all four modified Python files.
No runtime suite was repeated merely to write this report.

The original failure, intermediate failures, and six final results retain
separate source/command/log/XML receipts in task-root `audit/`. Raw private
helpers/logs are not all embedded in Git; the committed evidence records their
SHA-256 hashes and paths. The [earlier native vector report](optional-native-vectors-qualification-2026-10-01.md)
remains the historical Chroma-fixture checkpoint. Its former no-server boundary
is superseded by the loopback results above, with production/native limits still
explicit.
