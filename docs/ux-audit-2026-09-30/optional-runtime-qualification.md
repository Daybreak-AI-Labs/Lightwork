# Supplementary optional runtime qualification

Frozen source: `2c1e8a888eee011a2851836079b57c9929b8d745`. Executed on 2026-10-01 using Windows AMD64 and CPython 3.12.12. No product or test source changed for this cohort.

**239 distinct Python cases passed in each of ordinary and secure posture**, with one intentional absent-SymPy fallback skip in each. Another **24 TypeScript SDK Node tests passed** after rebuilding the exact frozen SDK source. These are supplementary local results, not a replacement for the consolidated full-suite result or customer release qualification.

| Complete test-file cohort | Ordinary | Secure |
| --- | ---: | ---: |
| Release runtime, LaTeX, Q2 batch 5, pandas query, tiered-storage codec, gRPC TLS, agent trust phase 3, Z3 glob proof | 151 passed, 1 intentional skip | 151 passed, 1 intentional skip |
| TypeScript plugin execution, including both actual built-SDK fixtures | 15 passed | 15 passed |
| Native detector, Unicode and perceptual-hash parity | 66 passed | 66 passed |
| Real unsigned synthetic PyInstaller onefile retained-helper relaunch | 1 passed | 1 passed |
| Prometheus computed metrics export | 6 passed | 6 passed |
| Total unique Python cases | 239 passed | 239 passed |

The [machine-readable receipt](optional-runtime-qualification.json) records every passed case, skip, wheel URL/hash, generated artifact hash and raw JUnit/log hash. Comparing exact case identities with the complete `29aee7e` ordinary JUnit establishes **113 previously skipped executable cases now passing**: 63 native, 29 LaTeX/release probes, 7 gRPC, 4 SymPy, 4 pandas, 3 zstandard, 2 built-SDK and 1 PyInstaller. Collection-level skips also expand into real Z3, pandas and metrics cases; the 113 count deliberately does not conflate collection placeholders with executable cases.

## Private dependency environment

The shared `.venv-audit` was not modified. A task-local `.venv-optional-full03` used its existing frozen baseline dependencies read-only through a `.pth` entry. All eight package source paths came from the separate exact-source clone. New distributions were installed only into the private environment from official PyPI binary wheels, with the existing CI constraints and an exact SHA-256 `--require-hashes --no-deps` lock after dependency resolution.

Qualified optional distributions: latex2mathml 3.81.1, SymPy 1.14.0, grpcio/grpcio-tools 1.84.0, protobuf 7.36.2, zstandard 0.25.0, pandas 3.0.6, pyarrow 25.0.1, z3-solver 5.1.0.0, Prometheus client 0.26.0, PyInstaller 6.22.3 and Maturin 1.14.1. The receipt includes the small transitive dependencies and all 19 official wheel hashes. Existing numpy 2.2.6, packaging 26.3, setuptools 84.0.0 and typing-extensions 4.16.0 were reused at their constrained baseline versions.

An initial eight-file run passed 150 cases, skipped the intentional fallback case and failed one gRPC case because its on-demand local stub generation also requires the declared `grpcio-tools` extra. Installing the matching compiler and protobuf wheels resolved the environment omission. The subsequent complete eight-file run passed in both postures. No assertion, transport policy or product implementation was weakened.

Tests used `python -m pytest -q -rs`, distinct `.t` homes and basetemps, removed provider/credential environment variables, disabled model fetching, and a Python audit hook rejecting external socket connections. Both `MAVERICK_TEST_SECURE_DEFAULT` and `MAVERICK_SECURE_DEFAULT` were explicitly 0 or 1; secure mode used a synthetic test key. No live provider, production, AWS, external database or model-download calls occurred. Test processes and compiler jobs stayed within the authorized two-job ceiling. Frozen-helper builds used a private PyInstaller cache and only the bounded synthetic fixture executable.

## Native wheel evidence and limits

The existing Rust 1.97.1 Windows GNU LLVM toolchain, bundled `rust-lld`, PyO3 0.29.2 and Maturin 1.14.1 successfully built the repository's `maverick-native` 0.1.0 ABI3 wheel. No MSVC, Windows SDK or host toolchain was installed. Cargo.lock was unchanged. Cached crates were copied into private scratch and verified against its checksums; only the two missing locked crates, fiat-crypto 0.3.0 and portable-atomic 1.14.0, were fetched from the official Rust registry and checksum-verified before an offline build.

The first setup attempt used an unstable linker-flavor spelling; using Rust's stable `ld.lld` spelling corrected the command. The first offline dependency pass then reported the two uncached crates. These were environment preparation failures, retained in the raw logs. The actual release compilation completed in approximately 79 seconds with one compiler job.

Maturin correctly reported the existing toolchain's `libunwind.dll` dependency. Its `--auditwheel repair` mode bundled that runtime into the local wheel, without rebuilding product source or installing DLLs system-wide. The repaired wheel's SHA-256 is `2bce5437391ca9a76be6a116b57744319cb066767133fff485596b8cb4da609f`. It was installed only into the private environment and imported with `python -I` without adding the Rust toolchain to that process's PATH. All 66 native parity tests then passed in both postures, with no native skips.

This verifies a local Windows GNU LLVM build, bundled import and parity against Python. It does not qualify the MSVC build, cross-platform wheels, a second clean reproducibility build, signed packaging or customer release installation. Those release gates remain separate.

## Exact SDK and frozen-helper coverage

The frozen TypeScript SDK source was rebuilt with the existing dependency copy containing TypeScript 7.0.2 and `@types/node` 26.6.3. Its dependency-lock hash, generated `dist/index.js` hash and 24-test Node log hash are recorded. Setting `MAVERICK_TEST_PLUGIN_SDK_BUILD` to that exact local output enabled the real Python-to-Node bridge fixtures. They verify one physical synthetic side effect in both normal completion and process-crash/unknown-outcome cases; the full 15-case file passed in each posture.

The PyInstaller test built and launched only its unsigned synthetic onefile helper. Both postures verified that the original extraction was deleted, inherited legacy state failed as expected, and the reset path re-extracted its own payload and completed successfully. This is concrete local bootloader-lifecycle evidence, not signed installer or clean-customer-machine qualification.

## Remaining environment boundaries

The intentional absent-SymPy fallback case skips when SymPy is installed; the baseline full environment retains that negative-path coverage. PostgreSQL and other service-backed cases belong to their separately recorded cohorts. No torch/model downloads, Chroma or other ML infrastructure, live connector SDKs, Docker/Firecracker provisioning, non-Windows host emulation or release signing was attempted here. Missing optional dependencies alone are not treated as permanent blockers; this record identifies only what was actually installed and executed.
