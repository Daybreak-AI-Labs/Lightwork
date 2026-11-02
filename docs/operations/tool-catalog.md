# Tool catalog registration

The built-in connector groups use `ToolDescriptor` declarations containing a
stable tool name, factory, and deferred eligibility. `declared_names` can read
the declarations without calling factories. Registry assembly invokes the
factories in their previous order and checks that each produced tool has the
declared name; it applies the existing ACL, containment, network, deferred
discovery, and governed-connector steps afterward.

`ToolRegistry.catalog_metadata()` describes registered tools that are not
hidden or denied by the registry ACL. Its `deferred_eligible` field describes
eligibility, not whether a tool is currently active or shown in a provider's
limited catalog. The method does not authorize an invocation. The normal
capability and execution checks still determine whether a tool can run.

The declarative groups currently cover the built-in deferred SaaS connectors.
Dynamically generated connector specifications, plugins, and the rest of the
registry still use their existing assembly paths. `base_tool_names()` still
builds the registry and can load configured extensions; declaration inspection
alone is not a substitute for the complete runtime catalog.
