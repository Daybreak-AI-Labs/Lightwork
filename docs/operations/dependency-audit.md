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

The OSV scan still reports these upstream dependency paths. They remain
release blockers under the existing blocking audit policy. No new ignore or
extension of an expired exception was added:

| Locked dependency | Dependency path | Upstream constraint |
| --- | --- | --- |
| `glib 0.18.5` | Tauri's Linux GTK3 integration | The reported iterator unsoundness is fixed in the incompatible 0.20 line. |
| `proc-macro-error 1.0.4` | Tauri's GTK build macros | Upstream maintenance advisory; no patched version in this dependency line. |
| `unic-char-property`, `unic-char-range`, `unic-common`, `unic-ucd-ident`, `unic-ucd-version` at `0.9.0` | Tauri's URL-pattern dependencies | Upstream maintenance advisories; no patched releases. |
| `deepmerge 4.3.1` | Svelte's Vite plugin | Reported prototype-pollution advisory; no published fixed version. |

The Tauri exceptions in `osv-scanner.toml` expired on October 31, 2026 and
are not a passing audit result. Moving to an incompatible GUI stack or
maintaining patched dependency forks requires a separate implementation and
platform validation. The scanner findings above identify dependency exposure;
they are not an application exploitability assessment.

Reproduce the dependency scan with the `audit` job in
`.github/workflows/ci.yml`. Keep the Python runtime audit, complete optional
pin audit, and cross-ecosystem OSV result separate: a clean Python report does
not clear the native or frontend build dependencies. Generate the release
SBOM from the isolated release-runtime environment, not the development venv.
