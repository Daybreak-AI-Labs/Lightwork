# Final09 Python qualification at the corrected source

All four complete Python 3.12.12 phases passed at unchanged source
`2decc28d5a2709ec1d362bdfb8499aaa13aacfce`: ordinary and serial heavy-concurrency
execution, each repeated with both secure-default flags enabled. Python 3.10
and 3.11 qualification remains separately tracked. These results do not imply
that every supported environment or external prerequisite has been qualified.

| Completed phase | Passed | Failed / errors | Skipped | Warnings | Pytest time | Controller time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Ordinary, four workers | 40,099 | 0 / 0 | 597 | 25 | 3,123.11 s | 3,127.90 s |
| Heavy concurrency, serial | 7 | 0 / 0 | 8 | 1 | 78.18 s | 87.81 s |
| Secure ordinary, four workers | 40,099 | 0 / 0 | 597 | 22 | 3,185.99 s | 3,190.82 s |
| Secure heavy concurrency, serial | 7 | 0 / 0 | 8 | 1 | 77.47 s | 87.19 s |

The eight heavy-phase skips are the same collection placeholders already
included in the ordinary phase's 597 skips. Each posture covers the same
40,106 distinct passing executable identities; repetition under the secure
posture does not add distinct cases. Both heavy selections deselected the
40,688 ordinary executable identities. Ordinary/secure identity-to-outcome
maps match exactly, as do both heavy-phase maps and all corresponding skip
sets. Both `MAVERICK_TEST_SECURE_DEFAULT` and `MAVERICK_SECURE_DEFAULT` were
`1` for the secure phases and unset for the ordinary phases.

The [JSON receipt](python-integrated-final09-2decc28.json) contains exact
commands, source/result receipts, JUnit counts, streamed report summaries,
warning details and SHA-256 hashes of every referenced run artifact. All four
phases finished with pytest/controller exit code zero, unchanged clean source,
no phase timeout, no controller/source-verification/cleanup errors and no
remaining pytest process. Their failure logs are empty, with no duplicate
call reports or failed setup/teardown reports. Execution used the native
Windows account, matching-source environments and fresh temporary directories.
The original command timeout defaults remain 180 seconds for ordinary and
600 seconds for serial heavy tests; existing per-test timeout markers remain
authoritative. Phase limits remain 7,200 and 1,800 seconds.

## The repaired failure and unchanged identity set

All 40,696 ordinary JUnit identities and the complete 597-skipped set exactly
match the [preceding final08 run](python-ordinary-final08-57a027a.md). The only
outcome change is the repaired flow-payload `[bytes]` case, from failed to
passed. Its call passed in 0.1975973 seconds on worker `gw3`, and in
0.1797480 seconds on worker `gw2` under secure defaults.

The [retained-fixture proof](flow-payload-fault-injection-57a027a.md) established
that the old literal-`X` replacement left that particular encrypted artifact
unchanged. The corrected test flips one bit and asserts a real change before
writing. Production integrity-check code, the original `FlowPayloadError`
expectation and test budgets are unchanged. The full final08 failure remains
in its historical receipt; it is not relabelled as a pass.

The seven PostgreSQL cursor variants remain skipped in this service-free
ordinary and secure runs. Their separate passing local PostgreSQL runs under both
postures, exact identity mapping and matching test/product blobs are recorded
in the final08 evidence. The other prerequisite and platform limitations also
remain separate from this integrated result.

## Why ordinary warnings increased from 23 to 25

Two additional deprecation warnings were reported, both from unchanged source:

- `test_plugin_permissions.py::test_ungranted_permission_skipped_when_enforced`
  reported the existing plugin API v1 deprecation from `plugin_manifest.py:206`.
- `test_msi_native_payload.py::test_real_embedded_msi_native_bytes_are_read_without_installation[None]`
  reported Python's existing `msilib` deprecation at line 56.

The relevant test and emitter Git blobs match final08. The five warning
families remain Starlette/AnyIO, `msilib`, plugin API v1, bare-string channel
replies and Click. The channel deprecation count remains five; one reported
attribution changed from the SMS deduplication case to the WhatsApp reply
case. The receipts do not establish why warning emission/attribution varied
between runs. No warning was suppressed to obtain a passing result.

The secure ordinary run emitted 22 warnings in the same five families. Its
summary contains one plugin API v1 warning instead of four: the permissions,
matrix and API-v2 test attributions present in ordinary mode are absent.
The channel warning count remains five, with the IRC attribution replaced by
the Threads polling case. The evidence establishes the reported differences,
not their process-level cause.

Each serial heavy phase emitted one Starlette/AnyIO `BlockingPortal` alias
deprecation. All warnings remain visible in the raw logs and JSON receipt;
the runs are not described as warning-free.

## Transport case and outstanding qualification

The previously failing bounded-queue transport case passed again in the
complete ordinary run. Its whole call lasted **5.0204485 seconds** on `gw2`,
and **5.3997987 seconds** on `gw3` under secure defaults.
That measurement includes admissions, the waiter, shutdown and assertions;
it is not the waiter's duration. The original five-second waiter and ordering,
durability and serial-execution assertions remain intact. The cause of the
[historical final07 timeout](transport-timeout-investigation-95aa61e.md)
remains unexplained.

Python 3.10 and 3.11 runtime qualification is tracked separately.
Installed-device, other-platform, compiler/container, provider and other
prerequisite boundaries remain explicit; passing these local phases does
not certify those environments.
