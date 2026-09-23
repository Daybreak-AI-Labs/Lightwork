# The knowledge plane

Per-domain document recall (RAG) for the workforce: a specialist agent grounds
its answers in the documents its department is allowed to see, via the
`knowledge_search` tool. Knowledge is an **opt-in extra** — the kernel never
requires `maverick-knowledge`, and RAG is off until you enable it.

```toml
[knowledge]
enable   = true
embedder = "local"     # hosted | local | deterministic
store    = "sqlite"    # sqlite | pgvector | qdrant
```

## Storage: pick a backend per deployment

One `VectorStore` protocol supports three backends. Operational limits and
reindex support differ; qualify the selected backend with your corpus.

| Backend    | When                                            | Notes |
|------------|-------------------------------------------------|-------|
| **sqlite** (default) | Single box, SMB, air-gapped | Brute-force cosine over local records; measure latency at the intended corpus size. |
| **pgvector** | Deployments already operating Postgres | Needs `maverick-knowledge[pgvector]`, the `vector` extension, and verified database isolation, backup and permissions. |
| **qdrant** | Dedicated retrieval infrastructure | Needs `maverick-knowledge[qdrant]`. Existing collections migrate by re-ingesting into a new collection; in-place reindex is refused. |

We do **not** ship a managed-only vector service as the default: the platform
sells "your data never leaves your boundary," so the knowledge store — the
client's most sensitive documents — must be self-hostable. A hosted backend can
be added later as an option, never the baseline.

### pgvector

```toml
[knowledge]
enable = true
store  = "pgvector"
dsn    = "postgresql://db-host/lightwork"   # credentials via MAVERICK_KNOWLEDGE_DSN / MAVERICK_PG_DSN env
dim    = 1024                               # must match your embedder's width
```

Per-workspace isolation is preserved: rows are namespaced by a hash of the
workspace, so one shared cluster still keeps tenants apart.

### qdrant

```toml
[knowledge]
enable  = true
store   = "qdrant"
url     = "http://qdrant:6333"     # or QDRANT_URL
dim     = 1024
# api_key via QDRANT_API_KEY
```

## Embedders

`hosted` (Voyage, needs `VOYAGE_API_KEY`), `local` (on-box
sentence-transformers, no key), or `deterministic` (a hashing stub — offline and
keyless, but low recall quality; for tests and smoke runs only). **Meaningful
semantic recall needs `local` or `hosted`.** Changing an embedder or model can
change the vector space even when its dimension stays the same. Chunks record
an embedding identity; retrieval and ingestion refuse a mixed or incompatible
collection instead of silently comparing unrelated vectors.

Existing untagged collections require an explicit migration. With the intended
embedding configuration active, run
`maverick knowledge reindex <collection> --tenant <tenant>` for a supported
SQLite or pgvector store. Reindex uses the existing complete chunk text and
preserves chunk IDs, provenance and content commitments. Vector replacement is
atomic against the captured collection state; a concurrent corpus change
requires a retry. This in-place operation is bounded to 100,000 chunks. A
pgvector store also requires the replacement vectors to match the existing
table dimension; changing dimensions requires a new store and an explicit
corpus migration. For Qdrant, ingest into a new collection and switch the
configured knowledge sources after verifying it; in-place reindex is refused
before embedding work begins.

Hosted identities bind the configured provider, model, endpoint, dimensions
and optional `[knowledge] revision`. That revision is an operator-supplied
identity, not independent proof of which remote weights served a request.
Changing provider weights without changing a published or configured revision
cannot be inferred from equal-sized vectors. Opaque custom embedder instances
do not share a persistent cache identity.

## Governance

Every chunk is provenance-stamped at ingest: `source` (the citation), `subject`
(the data-subject key, when the document belongs to one), `trust_tier`
(3 first-party … 0 external), `sensitivity`, `ingested_by`, `ingested_at`, and a
`doc_sha256` content hash. Ingestion is screened for prompt-injection on the way
in and recorded on the signed audit chain (`evidence_capture`).

Because chunks carry a subject, the knowledge plane participates in the platform's
compliance flows:

- **Right to erasure (GDPR Art. 17).** `maverick erase --channel X --user Y`
  removes the subject's document chunks along with their world-model rows, and
  the erase's signed audit event records the count.
- **Erasure verification.** `maverick erase-verify` folds the subject's residual
  knowledge-chunk count into its verdict. A subject is not "clean" while any of
  their documents survive in the vector store, and an unavailable configured
  knowledge store yields `INDETERMINATE` rather than an assumed zero.
- **Knowledge-only cleanup.** `maverick knowledge residual --channel X --user Y`
  and `maverick knowledge erase-subject ...` for targeted checks and removals.

Retrieval respects the compartment bulkheads: a pack's `knowledge_search` is
bound to its own `knowledge_sources`, so a finance specialist never retrieves
another department's documents.

## Starter corpora

`maverick knowledge seed` ingests a small, licensing-clean grounding corpus —
original plain-language summaries of public-domain frameworks (GDPR, EU AI Act,
NIST AI RMF, CCPA/CPRA), each anchored to the article it summarizes — into the
suite collections the shipped packs already point at. So a privacy/GRC
specialist can cite the governing regulation out of the box; customers layer
their own documents on top. Redistributable-restricted standards (e.g. ISO) are
deliberately excluded.

```
maverick knowledge seed --list          # show the shipped corpora
maverick knowledge seed                  # ingest them (keyless; idempotent)
maverick knowledge collections           # list collections + chunk counts
```
