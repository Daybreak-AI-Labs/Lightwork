# Control-plane replay signing qualification

The full secure run on `500fc87363a492def69722ef19d2cc518d171286` reported
`test_build_replay_timeline_and_chain`: expected `unsigned` or `no_log`, actual
`verified`. The same failure reproduced in an isolated clone of
`7d38f76ccc7c8b1752eaeb44d65dccfd0646d4d5`, with both
`MAVERICK_TEST_SECURE_DEFAULT=1` and `MAVERICK_SECURE_DEFAULT=1` set before
collection. The original failure is retained as
`audit/control-plane-before-secure-01.{log,xml,result.json}`.

This was an incorrect fixture assumption. The test removed
`MAVERICK_AUDIT_SIGN`, then asserted signing was disabled by default. The actual
writer's `_resolve_signing(None)` inherits `secure_by_default()` when no explicit
environment/configuration override exists. The secure-mode `verified` result
was correct. No signer, verifier, product default, or global test security
policy was changed.

Test-only commit `ab0117e8515c5c4e0967e7715a8c58351629f8e8` changes
`packages/maverick-dashboard/tests/test_control_plane.py`:

- Parameterize explicit unsigned and signed policies, setting only the local
  test's signing override. Require the exact expected status and signer state;
  missing log data is no longer accepted as a successful fixture outcome.
- Retain timeline entries, tool-call count, approval counts, and risk checks.
- Inspect real audit rows. Unsigned rows must contain no signing fields and
  must each verify as unsigned. Signed rows must contain the complete signing
  envelope and verify against an independently held, synthetic Ed25519 public
  key. The key is confined to the temporary test environment.
- Add real payload and signature modification cases. Each begins with a
  verified native chain, modifies one stored event, then requires both the
  pinned-key verifier and dashboard replay to identify the appropriate
  `bad_hash` or `bad_signature` failure. Replay must report `broken`, with a
  failed read-report verification. Existing anchor-ledger break checks remain.

| Complete module run | Passed | Failed / skipped | Pytest duration |
| --- | ---: | ---: | ---: |
| `control-plane-after-ordinary-01` | 27 | 0 / 0 | 37.09 s |
| `control-plane-after-secure-01` | 27 | 0 / 0 | 23.70 s |

Both runs executed the same test-file bytes committed as `ab0117e8`; the
ordinary run started with that test patch uncommitted. Secure mode sets both
flags before collection. These are 27 distinct cases executed under two
postures, not 54 distinct tests. Each run emitted one existing Starlette/AnyIO
deprecation warning; neither has a failure, error, or skip.

The complete affected module was selected through the private, source-matched
interpreter with `python -m pytest`, one xdist worker, the existing 180-second
test timeout, and optional timed traceback disabled. Inherited provider
credentials were scrubbed. All data, audit files, and signing keys were
synthetic and temporary; no live provider or production action was taken.
Ruff, Python 3.10 AST parsing, and whitespace checks passed for the changed
file. Raw logs, JUnit files, and result metadata remain under task-local
`audit/`; `control-plane-qualification-source.json` records the baseline,
committed patch, and file hash.
