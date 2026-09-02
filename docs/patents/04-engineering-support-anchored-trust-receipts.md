# Engineering Support Memo 04 — Externally Anchored, Offline-Verifiable Trust Receipts

> **Not legal advice.** This is an engineering-side technical support memo for
> patent counsel. It does not assess patentability, novelty, prior art, claim
> scope, inventorship, freedom to operate, or filing dates or deadlines.

## Purpose and boundary

This memo records the current implementation boundary for a portable Trust
Receipt v1 artifact and its optional external-anchor extension point. It is a
description of code and focused test evidence, not a statement that a receipt
claim is true or that a production transparency-log integration has been
operated.

## Technical problem

An auditor can receive a signed evidence artifact from an issuer without having
access to that issuer's application or audit store. Three different questions
must remain separate:

1. Did an explicitly trusted signing key sign these exact receipt bytes?
2. Was an exact signed receipt core bound to an independently verified
   transparency-log entry?
3. Are the application-level claims in the receipt true?

Conflating those questions is unsafe. For example, a receipt-embedded key does
not establish the issuer's identity, and a successful log-submission response
does not establish inclusion in a log. The current design keeps each assertion
at a distinct interface and refuses to elevate an unverified anchor reference
to an anchored result.

## Current technical architecture

### 1. Portable signed core

Trust Receipt v1 defines a bounded JSON envelope with a version, application
payload, payload SHA-256, and Ed25519 signature metadata. `lightwork-cjson-1`
is a byte-exact JSON encoding rule: sorted keys, compact separators,
ASCII escaping, no floats, no duplicate keys, and bounded depth/size. The
signature covers a context-separated canonical representation of the receipt
core, while `receipt_sha256` is calculated over that signed core for use by an
external anchor.

The standalone verifier parses only canonical input and independently checks
the payload digest, signature encoding, signature validity, and an explicitly
provided trusted public-key set. It is intentionally self-contained and does
not need to import the main runtime to verify a supplied file.

### 2. Optional anchor interface

The anchor adapter decomposes anchoring into two deployment-owned interfaces:

```text
canonical signed receipt core
        |
        +--> SHA-256 receipt digest --> injected submission transport
                                             |
                                             v
                                      normalized log response
                                             |
        optional anchor reference <----------+
                                             |
                                             v
                        injected inclusion-proof verifier + trusted log policy
                                             |
                                             v
                                anchored / submitted-unverified / unavailable
```

`RekorAnchorAdapter` has no default network client. With no injected transport
it reports an unavailable outcome. With a transport but no separately injected
inclusion verifier, a response is represented as submitted but unverified. It
reports an anchored outcome only after an inclusion verifier accepts a
request/response pair bound to the exact receipt digest. The offline receipt
verifier follows the same separation: an `anchor` object alone is indeterminate;
`require_anchor=True` refuses when no external verifier is supplied.

### 3. Existing receipt lineage remains distinct

The repository also contains deployment-local receipt construction and
verification for existing audit records. Trust Receipt v1 is deliberately a
separate portable format rather than a migration claim for every older receipt.
This distinction preserves the meaning of legacy verifier results while giving
an auditor-kit format a small independent boundary.

## Engineering variations to preserve for review

- The external log may be Rekor-compatible or another append-only transparency
  service, provided the transport binds the signed-core digest and the
  verifier checks an independently trusted proof.
- An implementation may distribute trusted issuer keys through a static audit
  package, a certificate policy, an enterprise trust store, or a key-rotation
  mechanism. The receipt's own public key must not be the sole identity
  authority.
- The log-proof verifier may verify an inclusion proof directly, verify a
  signed checkpoint plus inclusion proof, or use another explicit
  independently trusted proof protocol. A provider response alone remains
  insufficient.
- The signing side may use an HSM, KMS, remote signer, or another custody
  arrangement. That is operationally separate from the current verifier and
  must be documented by the deployment that uses it.
- An issuer may add application-specific claims to the payload, but their
  semantic truth needs evidence outside the receipt format.

## Focused test-vector and implementation evidence

| Evidence | What it exercises |
| --- | --- |
| `docs/specs/trust-receipt-v1.md` | Portable envelope, canonicalization, trust-key, and anchor-result contract. |
| `packages/maverick-core/maverick/trust_receipt_v1_verify.py:211-388, 501-676` | Canonical parser, core digest, strict Ed25519 verification, and verification-result construction. |
| `packages/maverick-core/maverick/trust_receipt_rekor.py:33-174` | Typed request/response, injected transport/proof interfaces, outcome states, and anchor-reference construction. |
| `packages/maverick-core/maverick/trust_receipt.py:216-311, 439-447` | Existing deployment-local receipt build/sign/verify path, kept separate from v1. |
| `packages/maverick-core/tests/data/trust_receipt_v1_vectors.json` | Canonical valid and tampered portable-receipt vectors. |
| `packages/maverick-core/tests/test_trust_receipt_v1_verify.py` | Valid/tampered vectors, RFC 8032 vectors, malformed canonical input, trust-set refusal, and fail-closed anchor requirements. |
| `packages/maverick-core/tests/test_trust_receipt_rekor.py` | No-transport, response-only, mismatched digest, malformed response, injected inclusion verifier, and error-refusal paths. |
| `packages/maverick-core/tests/test_trust_receipt.py` | Existing local receipt signing, key trust, anchor-depth, revocation, privacy, and correlation behavior. |

## Known limitations and non-claims

- The v1 foundation does not provide a production private-key custody design,
  HSM/KMS integration, external signer service, or key-rotation operating
  procedure.
- The adapter does not include credentials, a live Rekor client, a configured
  log trust root, or evidence of live provider interoperability. Its focused
  tests use injected test doubles.
- A valid signature says an explicitly trusted key signed the supplied bytes;
  it does not establish issuer identity, key custody, application-claim truth,
  or legal/audit sufficiency.
- An anchor reference is not an inclusion proof. A deployment must provide a
  reviewed proof verifier and its own trust policy before asserting anchoring.
- The portable v1 format is not an assertion that older deployment-local
  receipt schemas are interchangeable with it.
