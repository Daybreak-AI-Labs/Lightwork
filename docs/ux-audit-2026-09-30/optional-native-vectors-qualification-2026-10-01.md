# Optional native vector qualification — 2026-10-01

The optional local Chroma and Qdrant paths are now exercised with installed native
clients. Both postures passed **41 tests**, with **one intentional skip**, across
three complete files. No production code changed.

## Source and fixture correction

The initial immutable product/test source was
`500fc87363a492def69722ef19d2cc518d171286` in `qualification-final-04`.
With the optional dependencies installed, both ordinary and secure runs produced
39 passes, two failures, and one intentional skip. Both failures were the older
Chroma fixtures using `t1` / `t2`, below Chroma's documented three-character minimum.
Those fixtures also omitted vectors despite the adapter deliberately configuring
`embedding_function=None`, which requires precomputed vectors.

Test-only commit `b1bea937e59847e2021ca08d3bc3ecf82fda4092` uses valid collection
names and explicit synthetic vectors, disables telemetry in the two tests, and
strengthens the round-trip and deletion assertions to check exact IDs, document
content, and metadata. The adapter's behavior is unchanged. This commit is a
separate source boundary from the full-suite run on `500fc873`.

The documented client contracts are [Chroma collections](https://docs.trychroma.com/docs/collections/manage-collections)
and [Qdrant client local mode](https://pypi.org/project/qdrant-client/).

## Complete-file results

| Complete file under `packages/maverick-core/tests/` | Ordinary | Secure |
| --- | ---: | ---: |
| `test_recall_native_adapters.py` | 5 passed, 1 skipped | 5 passed, 1 skipped |
| `test_q2_2026_batch3.py` | 16 passed | 16 passed |
| `test_qdrant_store.py` | 20 passed | 20 passed |
| Total | **41 passed, 1 skipped, 7.74 s** | **41 passed, 1 skipped, 8.95 s** |

Secure runs set **both** `MAVERICK_SECURE_DEFAULT=1` and
`MAVERICK_TEST_SECURE_DEFAULT=1`; ordinary runs set both to zero. The existing
native-vector fixture explicitly sets `MAVERICK_ENCRYPT_AT_REST=0` in each posture.
These results prove the local adapter/recovery/tenant-filter contracts; they do
not assert encrypted native-vector storage coverage.

The native checks cover dimension-change failure retaining the previous document,
explicit recovery, rejected NaN vectors, deletion without late-retry resurrection,
and exact Qdrant tenant filtering for colliding normalized tenant names. The one
intentional skip is the Chroma parameter of the Qdrant-specific tenant-filter
test. It is not a missing dependency. No missing-package skips remain in these
three files.

`test_qdrant_isolation_live.py` still requires an external Qdrant server and was
not run. Full03 contained eight missing-package skips: six native parameters
(one now correctly classified as intentionally inapplicable) and the two older
Chroma tests. Seven previously unavailable cases therefore actually executed.

## Private dependency and execution boundary

Python 3.12.12 ran from `.venv-optional-vectors-500fc87`, with existing baseline
dependencies inherited read-only. Chroma **1.5.9** and Qdrant client **1.19.1**
were resolved from official PyPI. All **46** added distributions were installed
from binary wheels, using exact versions and SHA-256 hashes, into that private
environment only. See [the wheel lock](optional-native-vector-wheels-2026-10-01.lock)
and [the provenance record](optional-native-vectors-evidence-2026-10-01.json).
The lock is qualification evidence, not a change to supported product dependency
ranges. `pip check` returned `No broken requirements found.`

No shared interpreter, frozen checkout, credentials, deployment settings, server,
or host-wide SDK installation changed. Wheel URLs were verified to use
`https://files.pythonhosted.org/` and `.whl`; no source builds or model downloads
were used. Native operations used task-owned persistent local storage and
synthetic vectors. TGI/channel and remote Qdrant unit cases use their existing
fake clients.

The runner strips provider/account credentials, uses isolated HOME/TEMP and
MAVERICK_HOME, disables telemetry and model downloads, disables pytest caches and
bytecode writes, and installs a Python audit hook that refuses external DNS and
socket connections. A negative probe verified refusal before dispatch. Installed
distribution versions and paths were checked against every reported wheel.
The initial sandbox-account attempts could not traverse the wheel directories
created by the installer account and produced import/setup failures; those are
preserved as setup evidence, not product failures. Qualification then ran under
the private environment's owning account with the same network/credential guard.

## Reproducibility and artifacts

Task-root `run_optional_vectors.py` ran `python -m pytest -p optional_test_guard
-p no:cacheprovider` over the three complete files with task-owned `--basetemp`,
JUnit output, and `faulthandler_timeout=0`. Its source hash, the guard hash, source
SHA, exact command, flags, and wheel URLs/hashes are recorded in the evidence JSON.

Local artifacts under `audit/`:

- `optional-vectors-500fc87-resolve.json`, `-installed.json`, and `-wheels.lock`:
  official wheel resolution and verified installation.
- `optional-vectors-500fc87-readable-{ordinary,secure}.{log,xml,source.json}`:
  unchanged-source failures and native passes.
- `optional-vectors-b1bea93-{ordinary,secure}.{log,xml,source.json}`:
  corrected complete-file passes.
- `optional-vectors-environment.json` and `optional-vectors-pip-check.log`:
  installed paths, package versions, wheel provenance, network refusal probe,
  and dependency consistency.
- `optional-vectors-500fc87-{ordinary,secure}.{log,xml,source.json}`:
  initial account-ACL setup failures, excluded from qualification totals.

Ruff and `git diff --check` passed for the fixture correction. Both the immutable
source and the qualification clone were clean after the runs. The remaining
boundary is live Qdrant service qualification, not local adapter implementation.
