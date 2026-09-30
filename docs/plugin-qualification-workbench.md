# Plugin qualification workbench

Enable **Plugin qualification workbench** in Settings or the installer, then open
Plugins. Set the immutable `sha256:` ID of an already installed, reviewed local
Linux image containing the exact distribution, Lightwork core and `jsonschema`.
The workbench never pulls images, inherits provider credentials, mounts host work,
or falls back to host execution. The strict research runner verifies its created
container policy and cleanup. Its existing explicit Budget remains mandatory.

The workbench inspects entry-point metadata without importing plugin targets.
Missing/malformed manifests, incompatible API versions, denied permissions or
uninspectable bytes cannot qualify. A negative admission control explicitly proves
the runtime gate refuses a denied entry without import. That control does not claim
an arbitrary plugin obeys advisory permissions when run outside this sandbox.

For Python tool factories, enter synthetic sample arguments and the exact expected
JSON result. The container verifies installed distribution version/content, validates
the actual tool schema (including formats, with external schema references refused),
and executes that sample once. Receipts retain hashes, not input/output bodies.
The result must match the expected digest. Run timeout and cancellation checks with
a deliberately slow synthetic input; startup termination before observed plugin
dispatch is not a passing plugin interruption check. Whole-container cleanup must
be verified. Every operation ID is reserved durably before dispatch; refresh its
ledger entry after a lost response. It cannot be replayed. Interrupted results remain
unknown and never become live-service or write-access acceptance.

Publishing requires current passing sample, timeout and cancellation receipts and
a concrete affected-work/rollback plan. Installed bytes, manifest/policy, immutable
image or harness changes invalidate the scoped record. No package is installed or
rolled back by publication. The authorized workflow/task inventory links back to
native review; no other employee's private content is disclosed.

While this feature is enabled, Python task tools pin installed bytes and retain
used-plugin pins across task registry rebuilds/restarts. Drift refuses dispatch
instead of silently swapping the implementation. Current plugin admission policy
is rechecked. A stopped, owned task may be explicitly migrated to a current qualified
version; this never resumes it or replays old effects. In-flight work must finish or
be cancelled first. This is a dispatch guard, not a claim that side-by-side old plugin
packages are installed. Existing flow publication and approval authority remain in
force; direct flow-action execution and non-Python plugin kinds need separate pins.

Executable qualification currently covers Python `maverick.tools` factories.
Channels, skills, personas and TypeScript transports expose metadata or existing
runtime diagnostics; this workbench does not qualify their distinct execution
contracts. Their adapters and direct-flow version pinning remain implementation
follow-ups for the complete cross-plugin acceptance scope of LW-081. Existing
owned-process timeout/no-replay foundations are retained, not replaced with mocks.

The 1000-entry per-account ledger fails closed at capacity rather than forgetting
operation IDs and allowing a replay. Readiness is sandbox-only. Running native/live
acceptance, provisioning images and installing packages require their normal review;
none was performed in this implementation. Python/JS static checks passed; regression
and controlled-container tests are authored but unrun at the user's request.
Copilot does not yet expose these qualification mutations; use the Plugins review UI.
