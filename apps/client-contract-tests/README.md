# Local companion client contracts

Run with Node 24 and the exact development dependencies in `package.json`:

```sh
npm install --ignore-scripts --no-audit --no-fund
npm test
```

Dependencies may instead be installed in a disposable external directory with
`NODE_PATH` pointing to its `node_modules`; the tests always resolve the source
checkout relative to this directory. No test launches an IDE/browser profile,
contacts a provider, or starts a paid goal. Neovim tests run the actual Lua
source in Fengari with a controlled `vim` API; that is not native Neovim
qualification. See [the lifecycle contract](../../docs/client-request-lifecycle.md)
and the separate mobile/JVM tests for the broader acceptance boundary.

The audit adds `native-ownership.test.cjs` for CLI timeout/cancellation, secure
token migration, host/credential changes and safe external-client failures, plus
`test_external_examples.py` for approval gating, truthful finish and REST receipt
recovery. The latter uses pytest and requests from the Python development
environment. Local qualification is now authorized. All 60 Node cases and all 14 controlled
external Python example cases passed on 2026-09-30; two CLI budget-preview cases
also passed. The exact source, pinned dependency versions, log hashes and limits
are in [native-client-qualification.json](../../docs/ux-audit-2026-09-30/native-client-qualification.json).
These use controlled host/transport APIs, including real Lua source in Fengari.
They do not qualify installed IDEs, devices, accessibility or live providers.
