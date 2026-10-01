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
The call lasted 0.1790315 seconds. The original run subsequently completed with
40,098 passes, this one failure, 597 skips, and 23 warnings in 3,049.04 seconds
(3,053.92 seconds in the controller). Its source remained unchanged.

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

After the original full-suite phase ended, the entire corrected payload module
passed **18/18 ordinary** in 8.08 seconds and **18/18 secure** in 5.16 seconds,
with no failures, errors, skips, or warnings. These are the same 18 test identities
executed in two postures, including all six artifact-damage cases. Both runs used
the unchanged frozen source `2decc28d5a2709ec1d362bdfb8499aaa13aacfce` and Python
3.12. The secure run set both `MAVERICK_TEST_SECURE_DEFAULT=1` and
`MAVERICK_SECURE_DEFAULT=1`; the ordinary run inherited neither flag.

The corrected test has Git blob `088c100deade0506b007ebea8b466b92cac3c8e4`.
The production payload implementation retains Git blob
`ceb2f9216ee1fc37ead8e22c5f3587528453aabe`. Source/result receipts, complete JUnit
test identities, logs, streamed reports, and empty failure logs were independently
checked; their hashes and commands are preserved in the
[machine-readable evidence](flow-payload-fault-injection-57a027a.json).

The private runner change only adds the secure probe choice, flags, and target
branch. Its existing 90-second per-test probe timeout remains unchanged. No
production assertion or test assertion was relaxed. The corrected module passes
close this specific no-op fault-injection failure; they do not turn the original
full run into an all-green result or replace the remaining final-09 qualification.

A targeted read-only search found no additional fixed-byte suffix replacement
on randomized or encrypted blobs. Related attachment, crypto-bound,
privacy-record, unified-audit, and goal-download tests already use bit flips.
This was a bounded pattern review, not an assertion that every fault-injection
test in the repository has been exhaustively verified.
