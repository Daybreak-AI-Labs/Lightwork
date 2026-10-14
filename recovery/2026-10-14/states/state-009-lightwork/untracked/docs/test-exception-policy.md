# Pytest exception policy

Lightwork treats every tracked or non-ignored untracked pytest skip, conditional
skip, import-based skip, and expected failure as reviewed test debt. The authoritative inventory is
`qa/pytest-exceptions.json`; `scripts/verify_pytest_exceptions.py` compares it with the
Python syntax in Git's cached and non-ignored untracked file set and fails closed on any
addition, removal, move, reason change, condition change, or stale record. Including new
files before commit prevents a locally green inventory from becoming stale only after the
change is committed.

The current reviewed snapshot contains 444 occurrences. Each record binds the exact source
module to a reason, GitHub owner, required environment, production-critical
classification, and either an expiry or a substantive permanent rationale. This is an
inventory of test exceptions, not evidence that every optional environment or live service
has been exercised on a particular release candidate.

## Rules

- `pytest.skip`, `pytest.importorskip`, `pytest.xfail`, and the `skip`, `skipif`, and
  `xfail` markers are scanned through normal and aliased pytest imports. String examples
  and generated test templates are not executable occurrences and are ignored.
- A source reason is mandatory. The inventory retains a SHA-256 fingerprint of the complete
  normalized UTF-8/LF module bytes, so changing a helper, condition, or executable branch
  while keeping the same displayed reason still requires review. Path, line, column,
  exception kind, and reason separately bind the individual occurrence.
- Every `xfail` must say `strict=True`. Temporary entries expire no more than 120 days after
  the inventory review; an expired entry fails the gate. A non-expiring xfail must carry an
  explicit rationale beginning with `PERMANENT:`. There are no permanent xfails in the
  current inventory.
- Production-critical test paths are defined in the verifier and repeated byte-for-byte in
  the policy file. A dependency-, required-tool-, or external-service-based exception in those paths must
  name a concrete GitHub Actions job and at least two required tokens. The verifier checks
  that the job and tokens still exist. This prevents the broad default suite from silently
  treating an optional-dependency skip as production coverage.
- Environmental platform and host-capability skips remain visible and reviewed. They do not
  claim that every OS or kernel capability ran in the current local validation.

The `production-critical-optionals` CI job installs the gRPC and durable-queue extras,
installs a checksum-verified Helm binary, verifies `ssh-keygen`, and runs the focused
dependency, Helm, release, link-safety, and POSIX custody suites. Existing cryptography,
FastAPI, workspace-package, and Postgres exceptions bind to the complete deterministic test
job or the live Postgres job. The lint job runs the inventory verifier before other
repository gates.

## Review workflow

Run these commands from the repository root:

```text
python -I scripts/verify_pytest_exceptions.py --root .
python -I scripts/verify_pytest_exceptions.py --root . --scan
python -m pytest -q packages/maverick-core/tests/test_pytest_exception_policy.py
python -m ruff check scripts/verify_pytest_exceptions.py packages/maverick-core/tests/test_pytest_exception_policy.py
```

`--scan` is read-only and emits the exact repository occurrences, locations, reasons,
fingerprints, strictness, and dependency signals as JSON. Inventory entries stay in stable,
ordered, contiguous ID order; the verifier matches them one-to-one against the exact source
occurrence-key set rather than assigning identity from source-file order. A moved occurrence
therefore keeps its ID while its reviewed location and module fingerprint change, and a new
occurrence receives the next ID. When an exception legitimately changes, review the output,
update `qa/pytest-exceptions.json`, and rerun the verifier. Do not copy an old source
fingerprint or classify a production-critical environment as a platform condition to make
the gate pass.

Release governance treats these three files as one protected QA-03 contract:

- `qa/pytest-exceptions.json`
- `scripts/verify_pytest_exceptions.py`
- `docs/test-exception-policy.md`

The production surface declares the exception-governance feature, release state binds the
exact inventory, readiness evidence records the local adverse-path result, and both release
authority workflows run the verifier before mutation. The versioned inventory is copied
byte-for-byte into the signed release governance packet. This integration proves the
repository policy and local regressions; it does not claim that hosted CI or optional live
services ran for the current release candidate.
