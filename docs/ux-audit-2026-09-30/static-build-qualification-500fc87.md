# Consolidated source: static and build qualification

Source: `500fc87363a492def69722ef19d2cc518d171286`. The isolated checkout remained
clean before and after all checks; builds used an external export of that exact
Git object, preserving the concurrent runtime-test source.

All **26 checks passed**: Ruff, Vulture, the 16 offline contract/governance gates,
and wheel builds for all eight packages. All eight ZIP integrity and SHA-256
checks passed. Every one of the **4,826 packaged payload files** matches the
exact Git export. Source-archive SHA-256:
`dbdc7951f0a21fc65db16cc0c65bf2060a48dafab9cc73ecbce5a5d064ca710b`.

Task-local evidence is retained in `audit/qualification-final-04-contracts/`:
`source.json`, `results.json`, individual logs, `wheel-sha256.json`,
`wheel-payload-verification.json`, `summary.json` and the built artifacts.
The matching interpreter loads all eight package roots from this same checkout.
No shared dependency, deployment configuration or credential changed.

The initial sandbox-account artifact read was denied by the native build
account's file permissions. Verification succeeded under that original native
account; file ACLs were not changed. The failed read is not a failed wheel check.

These results qualify the recorded source and local build environment. Runtime
tests, browser journeys, signed release trust, clean-machine installation and
live services require their separate evidence; the wheels were not published.
