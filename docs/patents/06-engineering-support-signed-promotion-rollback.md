# Engineering Support Memo 06 — Signed Promotion Ladder, Durable Recovery, and Rollback

> **Not legal advice.** This is an engineering-side technical support memo for
> patent counsel. It does not assess patentability, novelty, prior art, claim
> scope, inventorship, freedom to operate, or filing dates or deadlines.

## Purpose and boundary

This memo records the current technical components used to bind a human approval
to a promotion candidate, persist a promotion transition, recover incomplete
transitions, and record rollback. It distinguishes a signed approval from the
local promotion-journal integrity mechanism and from a future externally
anchored receipt.

## Technical problem

A self-modifying system should not claim that a new artifact was promoted until
the intended artifact is observed live and the durable promotion record exists.
It must also avoid reusing an approval for a changed payload, losing a prepared
transition on process failure, or treating a rollback intent as a completed
rollback. A reviewable technical record needs to retain enough identity and
state information to distinguish prepare, commit, abort/recovery, and rollback
without disclosing the full sensitive artifact or approval signature.

## Current technical architecture

### 1. Approval binds candidate, rung, and payload content

`ApprovalRequest.for_candidate` derives the bytes to approve from a candidate
identifier, promotion rung, and SHA-256 of the actual candidate payload when
one is present. It does not trust a candidate-supplied payload-digest field if
the real payload can be hashed. `sign_request` produces an Ed25519 signature,
and `verify`/`verify_candidate` return an approver key identifier only when the
signature verifies against an explicitly trusted public-key set. The signing
function is documented for operation outside the agent process; that deployment
separation is an operational requirement rather than a property proven by the
ledger.

### 2. Artifact-pinned write-ahead transition

An `ArtifactRevision` contains a logical artifact identity, version, and
SHA-256. `PromotionLedger.prepare` persists a write-ahead transaction with a
before/after artifact pair. A transaction becomes committed only when
`PromotionLedger.commit` is given the exact intended live artifact. If an
incomplete transition is found, abort/recovery paths require observed artifact
state before choosing a terminal state. A rollback is appended as its own event
and is not reported as complete without the associated durable receipt.

```text
candidate + exact approval request
                 |
                 v
            prepare(before, after) -- durable intent
                 |
         observe exact artifact state
           /                         \
          v                           v
commit(after)                  abort/recover(before or indeterminate)
          |
          v
append rollback event if a committed record is later rolled back
```

### 3. Authoritative local journal and compatibility projection

When configured with a path, `PromotionLedger` treats `<path>.journal` as the
authoritative append-only, hash-chained JSONL record and retains the historical
JSON list as a compatibility projection. The journal reader validates canonical
event content, per-event SHA-256, previous-hash linkage, event type, and
transition consistency. The append path flushes and fsyncs the journal before
the compatibility projection is updated; a stale projection can be repaired
from the journal.

### 4. Digest-only receipt/projection boundary

The read-only `evolution_ledger` module can project promotion and rollback
lineage into digest-only entries and construct/verify a promotion-receipt
representation. It exposes a signer key identifier and optional recorded
snapshot digest when present, rather than copying artifact locations, approval
signatures, or raw candidate content. It explicitly refuses to represent a
workspace snapshot identifier as a content digest.

## Engineering variations to preserve for review

- A promotion authority may use a different append-only store, a database
  transaction log, a transparency log, or a signed Merkle structure, provided
  it keeps prepare/commit/abort/rollback states and pins the observed artifact.
- The approval binding may use Ed25519, another signature algorithm, a remote
  signer, or a multi-party approval policy, provided it binds the actual
  payload/content digest and promotion rung rather than mutable display text.
- The read-only product projection can use keyed privacy-preserving digests,
  encrypted references, or an auditor-scoped disclosure bundle. A bare digest
  remains a reference, not independent evidence that the source exists.
- Snapshot provenance can carry a true content digest, a signed manifest, or a
  build attestation. A snapshot label or numeric snapshot ID must not be
  promoted to a content-integrity assertion.

## Focused test and implementation evidence

| Evidence | What it exercises |
| --- | --- |
| `packages/maverick-core/maverick/approval_signing.py:81-186` | Candidate/rung/payload-digest approval message, external-signing seam, trusted-key verification, and signer key identifier. |
| `packages/maverick-core/maverick/self_improvement.py:228-405` | Promotion record, content-addressed artifact revision, write-ahead transaction, and ledger error boundary. |
| `packages/maverick-core/maverick/self_improvement.py:1732-2016, 2273-2657` | Authoritative journal, prepare/commit/abort, rollback, journal verification, fsync append, and compatibility-projection repair. |
| `packages/maverick-core/maverick/workspace_snapshot.py:55-120` | Workspace archive/snapshot creation and bounded failure behavior; it does not supply a content digest in its manifest. |
| `packages/maverick-core/maverick/evolution_ledger.py:1-20, 232-269, 273-345, 678-1119` | Read-only digest projection, recorded-digest handling, promotion receipt construction/verification, and explicit evidence limitations. |
| `packages/maverick-core/tests/test_approval_signing.py` | Approval signature verification and actual-payload binding behavior. |
| `packages/maverick-core/tests/test_self_improvement.py:243-297, 392-505, 508-684` | Rollback idempotency, prepared/committed visibility, recovery, journal failure/recovery, hash-chain behavior, and concurrent retry. |
| `packages/maverick-core/tests/test_workspace_snapshot.py:51-128` | Snapshot/restore and corrupt-snapshot failure behavior. |
| `packages/maverick-core/tests/test_evolution_ledger.py:52-241` | Digest-only/read-only projection, transaction artifacts, receipt signature verification, invalid approval status, and snapshot-ID limitation. |

## Known limitations and non-claims

- The promotion journal is a local hash-chained authority, not an externally
  anchored transparency log or an independent third-party audit service.
- A valid approval signature identifies a trusted key under the configured
  trust set; it does not by itself establish a natural person's role,
  employment, intent, or approval-process compliance.
- Current workspace snapshots expose an ID and archive metadata, not a native
  content digest. The digest-only projection reports that omission rather than
  inventing a snapshot SHA-256.
- The projection reads caller-supplied source records and does not repair
  ledgers, rerun a learning suite, or independently attest mutable dream or
  hindsight data.
- The test evidence is local/focused. It is not evidence of production signer
  custody, live deployment rollback, a CAB process, or an externally operated
  audit exercise.
