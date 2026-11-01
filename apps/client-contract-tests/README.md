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

`native-ownership.test.cjs` covers CLI timeout/cancellation, secure token
migration, host/credential changes and safe external-client failures, and
`test_external_examples.py` covers approval gating, truthful finish and REST
receipt recovery. The latter uses pytest and requests from the Python
development environment (`python3 -m pytest apps/client-contract-tests`).
These use controlled host/transport APIs, including real Lua source in Fengari.
They do not qualify installed IDEs, devices, accessibility or live providers.
