# Complete secure-default checkpoint

Frozen source `500fc87363a492def69722ef19d2cc518d171286` completed the entire
ordinary-selection suite with both secure-default flags: **40,054 passed,
three failed, 558 skipped**, zero errors and zero worker losses. Pytest took
3,936.11 seconds; the isolated helper completed in 3,940.97 seconds. Four xdist
workers used the original 180-second per-test cap and repository-specific
overrides. Fatal traceback reporting remained on; only optional timed stack
dumps were disabled. Heavy-concurrency tests are a separate serial phase.

This is a completed failed qualification result, not a green-suite claim.
The [receipt](complete-secure-500fc873.json) retains exact source/process
metadata, all three failing identities and raw log/JUnit hashes. The later
repairs have separate complete-module evidence:

| Failed expectation | Correction and executed follow-up |
| --- | --- |
| Theme save expected only the old partial palette | Complete accessible palette asserted; 417 passed and two POSIX skips in each posture |
| Replay test assumed an absent override means unsigned | Explicit signed/unsigned policies, pinned-key verification and tamper controls; 27 passed in each posture |
| Streaming test inherited encrypted storage | Explicit plaintext streaming plus sealed roundtrip and GCM-tag rejection; nine attachment/budget cases passed in each posture |

Those passing groups overlap this suite and are not added to its unique count.
The large-corpus knowledge case and all other 65 cases in its module passed;
the earlier ordinary worker-loss cause remains an inference documented in
`knowledge-worker-exit-500fc873.md`.

The later SQLite admission repair and active current-Qdrant compatibility
repair are outside this frozen source. Fresh consolidated runtime qualification
remains required. The [skip reconciliation](skip-reconciliation-7d38f76.md)
records supplemental native/optional evidence without converting unrun platform
or external-service gates into passes.
