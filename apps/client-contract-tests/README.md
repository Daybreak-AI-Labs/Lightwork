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
