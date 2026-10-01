# Deterministic corruption in the flow-payload regression test

The `bytes` case failed in the final-08 ordinary run because its fault injection
did not alter the retained encrypted artifact. The production integrity guard
remains unchanged. The test now flips the final byte's low bit and explicitly
asserts that the damaged bytes differ before writing them; the original
`FlowPayloadError` expectation remains in place.

## Retained-fixture proof

The original source was `57a027adfdd8df955d33aaea88ec7089aafc6c60`. Worker `gw1`
reported `DID NOT RAISE FlowPayloadError` for
`test_unavailable_or_mismatched_artifact_never_falls_back_to_preview[bytes]`.
The call lasted 0.1790315 seconds. The original run was still active when this
investigation was recorded; these notes do not claim its overall outcome.

A read-only probe of its retained synthetic fixture found a 35,159-byte artifact
whose final byte was `0x58`, the byte for `X`. Its SHA-256 was
`ac70f2c20dcae1f529f2ee9d1bcbd248eb16ef92492ba606edf5ff2aa620ce63`, exactly matching
the stored `data_state.sha256` reference. Reapplying the original expression
`blob[:-1] + b"X"` in memory produced identical bytes and the same digest. This
establishes that the attempted corruption was a no-op for this exact fixture.

The corrected expression, `blob[:-1] + bytes([blob[-1] ^ 1])`, changes one bit
for every possible final byte while preserving the artifact length. Applied
only in memory to the retained fixture, it produced a distinct digest,
`448c44e5a756bb8314ee9401ef7528f958cba4d51a22be59a0343d3e401c0c8a`.
The probe did not decrypt payloads, read keys, or modify the original fixture.
The row's bytes and artifact size/modification timestamp were checked for
stability during the read.

`flow.payloads.decode` checks artifact length and SHA-256 against the stored
reference before decryption. Accepting the unchanged artifact is consistent
with that contract. No production change, denial-assertion relaxation, test
timeout increase, or exception suppression is part of this repair.

## Validation boundary

The changed test has not yet been executed. Focused execution is held until
the active full-suite phase ends, to avoid competing with that run. The original
failure and fixture metadata remain recorded in the
[machine-readable evidence](flow-payload-fault-injection-57a027a.json).

A targeted read-only search found no additional fixed-byte suffix replacement
on randomized or encrypted blobs. Related attachment, crypto-bound,
privacy-record, unified-audit, and goal-download tests already use bit flips.
This was a bounded pattern review, not an assertion that every fault-injection
test in the repository has been exhaustively verified.
