# Lazy enterprise connector executors

The declarative enterprise connector catalog separates immutable descriptions
from executors. Registry construction, schema serialization and `find_tools`
search do not construct these REST or GraphQL executors. The first authorized
invocation constructs only the selected connector and its governed wrapper.
Subsequent invocations reuse that registry's executor.

This internal refactor applies automatically to the bulk declarative catalog.
Handwritten tools, context-dependent tools, plugins and MCP registrations keep
their existing construction paths. The `deferred_tools` setting still controls
which schemas the model sees; it does not control executor construction.
An authorized hidden tool remains callable without activation.

## Identity and policy

- Immutable descriptors, JSON-compatible schemas and lexical search tokens may
  be shared across registries. Copy schema metadata before changing it.
  Built-in descriptor changes take effect after process restart; private catalog
  specification lists are not a runtime configuration API.
- Each registry owns its executors. Actor, goal, enterprise policy and credential
  authority are not cached globally. Connectors still resolve live credentials at
  the request boundary, under the current tenant and invocation principal.
- Discovery inspects descriptors. It never runs a connector or requests approval.
  Current ACL restrictions apply to discovery, activation and dispatch. Existing
  rate-limit, cache, retry, budget, killswitch and governance boundaries remain in
  their original dispatch order.
- Governance uses the existing wrapper around the real executor on first use.
  Receipts retain the real connector's implementation provenance. Writes still
  require approval and the existing conditional revision contract where applicable.
  Explicitly configured rate-limit wrappers retain their current eager policy
  wrapper setup, while their connector executor remains lazy.

## Failure and recovery

Construction is synchronized per tool instance. Concurrent first invocations
share one construction attempt. A factory returning inconsistent metadata is
rejected before its executor can run. Construction failures return a stable,
sanitized error and are remembered for that instance, preventing repeated broken
initialization. Fix the installation and create a new registry, or explicitly
replace the registered tool, to retry construction. No raw factory exception text
is retained in that failure diagnostic.

Budget and killswitch stop signals retain their control-flow behavior. Normal
execution errors are still handled by the existing tool dispatch policy; they
are not stored as construction failures. This does not change external-effect
idempotency or authorize replay of writes.

## Validation scope

`test_lazy_tool_executors.py` checks the full declarative schema/order contract,
metadata-only discovery, first-use construction, concurrent success and failure,
current ACL denial, hidden invocation, streaming dispatch, rate limits,
governance provenance and per-registry/per-tenant bindings. Existing connector,
governance, discovery and tool-cache suites cover their unchanged behavior.

Local construction measurements should include both the first registry, which
compiles immutable metadata, and later registries that reuse it. A reduction in
unused executor construction does not imply faster provider responses or lower
customer request latency in every workload.
