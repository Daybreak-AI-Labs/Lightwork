# @maverick/plugin-sdk

Author Lightwork agent tools in TypeScript. A plugin is a plain Node script:
Lightwork spawns it, discovers its tools, and calls them over stdio.

## Writing a plugin

```ts
import { defineTool, servePlugin } from "@maverick/plugin-sdk";

const greet = defineTool({
  name: "greet",
  description: "Greet someone by name.",
  inputSchema: { type: "object", properties: { who: { type: "string" } }, required: ["who"] },
  handler: (args) => `Hello, ${args.who}!`,
});

await servePlugin([greet]);
```

Compile it (`npm run build` here is just `tsc`) and you have a plugin.
Handlers return a string (or a promise of one); a thrown error becomes an
`"ERROR: <tool> failed: <message>"` result string, the same convention
Lightwork's built-in Python tools use. Log to stderr (`console.error`) only —
stdout belongs to the protocol.

## How Lightwork loads it

Opt in via `~/.maverick/config.toml` (the command is an argv list, run with a
secret-scrubbed environment — your plugin never sees provider API keys):

```toml
[plugins]
ts = [["node", "/abs/path/to/plugin.js"]]
```

`maverick.ts_plugin_host.load_ts_plugin(command)` runs `command --describe` to
read the manifest and builds one Lightwork tool per entry. Tool calls go to a
single persistent child process, started lazily. Each call has one deadline,
including lock waiting, input writes and response reads. A crash after possible
dispatch returns an unknown outcome and is never automatically replayed. A new,
deliberate invocation can start a fresh child. Reconcile uncertain external
effects before making that decision.

## Wire protocol (`maverick-plugin/2`)

NDJSON uses UTF-8, with a 1-MiB request/response frame cap. The manifest advertises
version 2 and `execution.replay: "never"`. Requests and replies look like:

```json
{"protocol":"maverick-plugin/2","id":"logical-call-id","attempt":1,"replay":"never","deadline_unix_ms":1893456000000,"tool":"greet","args":{"who":"Lightwork"}}
{"id":"logical-call-id","attempt":1,"effect":"completed","result":"Hello, Lightwork!"}
```

The deadline is an absolute Unix time in milliseconds; a caller must supply a
future deadline for each invocation. The example is illustrative, not a preset.
Replies distinguish `not_started`, `completed` and `unknown`. An exception after
handler entry has an unknown effect outcome even when an error message is
available. IDs correlate calls; the SDK provides no durable deduplication and
does not automatically retry. A protocol failure has an `error` field; a handler
failure retains the `ERROR:` result convention.

Handlers may accept a second `InvocationContext` argument with `requestId`,
`attempt`, `deadlineUnixMs`, `signal` and `replay`. Pass `signal` into cancellable
I/O. The SDK signals deadline expiry but continues to own that handler until its
cleanup finishes, preserving serialized effects. The Python host owns hard
process termination if the deadline is exceeded. Neither action rolls back
already completed external effects.

Version 1 requests still receive version 1 responses, and existing one-argument
handlers keep working. The host also accepts legacy version 1 plugins with the
same conservative no-replay policy. Arbitrary stdout logs are not protocol
messages: use stderr, which the host drains and bounds without exposing it.
Output backpressure is awaited before another handler starts; oversized or
unterminated frames are rejected.

See [process ownership and limitations](../../docs/operations/owned-cli-and-plugins.md)
for host limits, cleanup behavior and operating-system constraints.

## Structured page context

The Lightwork browser extension (`extensions/browser/`) can attach a bounded,
observe-only accessibility/DOM snapshot of the active page to a goal. This SDK
exports types and a safe parser for consuming that snapshot in a plugin:

```ts
import { parseStructuredPageContext, summarizePageContext } from "@maverick/plugin-sdk";

const ctx = parseStructuredPageContext(args.page); // object or JSON string
if (ctx) {
  console.error(summarizePageContext(ctx)); // logging goes to stderr
  // ctx.elements: interactive controls (name/role/tag/selector/type/value)
  // ctx.landmarks: landmarks + headings; ctx.lang/counts/truncated metadata
}
```

`parseStructuredPageContext` validates untrusted input and re-applies the
extension's caps (≤60 elements, ≤25 landmarks, per-string length limits),
returning `null` for anything that isn't a usable snapshot — so a plugin never
trusts oversized or malformed page context. See `StructuredPageContext`,
`PageElement`, and `PageLandmark` for the shapes.

## Developing

```sh
npm install   # dev deps: typescript + @types/node
npm test      # tsc && node --test
```

The `.ts` sources are authoritative and committed; `dist/` is build output.
