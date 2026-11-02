# Dependency audit and release blockers

The November 2, 2026 dependency review raised the runtime minimum to Python
3.11 so the release cohort can use Pillow 13.0.0. Python 3.11 and 3.12 remain
the tested interpreter window. Recreate an existing Python 3.10 virtual
environment with a supported interpreter before upgrading; an in-place package
upgrade cannot replace its interpreter.

The reviewed updates include Pillow, filelock, the Go toolchain, Tokio,
WebPKI, the Windows desktop updater and its ZIP dependency, the installer
frontend toolchain, and the TypeScript MCP example's transitive dependencies.
The checked-in branch ruleset drops Python 3.10 checks. Apply that ruleset
change to the repository's active rules before merging a PR whose workflow
no longer emits those checks; changing the JSON file alone does not update
GitHub's active rules.

The desktop dependency review now includes local source fixes where compatible
registry releases are unavailable:

| Previously reported dependency | Resolution |
| --- | --- |
| `glib 0.18.5` | Backport the upstream mutable out-pointer fix, preserving GTK3 compatibility. |
| `proc-macro-error 1.0.4` | Move both GLib and GTK3 macro adapters to maintained `proc-macro-error3 3.1.1`. |
| Five `unic-* 0.9.0` crates | Update `tauri-utils` to 2.10.2, which uses `urlpattern 0.6.0` and ICU. |
| `deepmerge 4.3.1` | Use a private compatibility fork that rejects prototype-control keys in both operands. |

The local copies retain upstream licenses and provenance, with a checked
inventory of source hashes and behavioral regression tests. See
[the dependency patch inventory](https://github.com/Daybreak-AI-Labs/Lightwork/blob/main/vendor/README.md)
for the exact changes and maintenance/removal conditions. These copies create
a maintenance obligation until compatible upstream fixes can replace them.

There are no advisory exceptions in `osv-scanner.toml`. The audit first verifies
local source integrity and application overrides, then scans all registry
surfaces, including the dependency regression harness. OSV still reports the local GLib version as an affected registry version.
The audit records only that exact backport as repaired after verifying the
source hashes and local Cargo resolution; both the raw findings and remediation
record remain attached to CI. Registry copies, new advisories, source drift and
scanner errors remain blocking. Optimized native regressions and frontend
tests cover the specific repaired behavior.

Reproduce the dependency scan with the `audit` job in
`.github/workflows/ci.yml`. Keep the Python runtime audit, complete optional
pin audit, and cross-ecosystem OSV result separate. Generate the release
SBOM from the isolated release-runtime environment, not the development venv.
