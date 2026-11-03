# Design Spec: MCP Server Registry

**Status:** Discovery and HTTP configuration install are available; registry-supplied local commands are refused. · **Related:** [`catalog.md`](./catalog.md), [`skill-index.md`](./skill-index.md)

## 1. Problem

Lightwork can *consume* external MCP servers once they're in
`[mcp_servers.<name>]` (stdio via `MCPClient`, remote HTTP via
`StreamableHttpMCPClient`). What was missing is **discovery +
install**: `maverick mcp-registry browse` / `add <name>` supports discovery
and remote HTTP configuration. Local commands require direct administrator
configuration because an executable hash does not authenticate its workload.

OAuth 2.1 (the other half of B2) is out of scope here — it needs real accounts
to validate. Static-bearer remote servers already work via `auth_token`.

## 2. The registry is a federated catalog

A registry is a self-hostable JSON index served at `<base>/mcp/index.json`,
reusing the generic [`catalog.py`](./catalog.md) infra (fetch + 6h cache +
SSRF-guarded `guarded_urlopen` + https-only + stale-serve + multi-index merge,
earlier index wins). Point `[mcp_registries] indexes` at your own base(s).
The built-in default index (`DEFAULT_MCP_REGISTRIES`) is not yet published;
configure your own under `[mcp_registries] indexes`.

```toml
# ~/.maverick/config.toml
[mcp_registries]
indexes = ["https://registry.example.com/catalog", "https://internal.acme/catalog"]
```

## 3. Index schema (`<base>/mcp/index.json`)

```json
{
  "schema_version": 1,
  "kind": "mcp",
  "entries": [
    {
      "name": "github",
      "version": "1.0.0",
      "summary": "GitHub MCP server (issues, PRs, code search).",
      "author": "modelcontextprotocol",
      "verified": true,
      "spec": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
        "pin_sha256": "<optional: hash of the resolved executable>"
      }
    },
    {
      "name": "acme-remote",
      "version": "2.0.0",
      "summary": "Acme's hosted MCP server.",
      "spec": {"url": "https://mcp.acme.com/sse", "headers": {"X-Tenant": "acme"}}
    }
  ]
}
```

**Why the spec is inline (vs. skills' fetched `SKILL.md` + `sha256`).** An MCP
server's installable artifact is *configuration*, not a content file. There's
nothing to fetch and hash — the actual server binary is resolved at spawn time
(`npx`/`pip`/a URL). So the entry carries the `spec` (a `[mcp_servers.<name>]`
dict) directly, and `source`/`sha256` are optional. `CatalogEntry` gained an
optional `spec: dict` field for exactly this; content kinds (skills, personas)
leave it empty and keep fetching `source`.

## 4. Trust model

- The index is **curated** (a PR against the registry repo adds an entry) and
  served over **https** through the SSRF guard.
- `verified` is **self-asserted by the index** and is NOT a trust signal (the
  index host is unauthenticated), exactly as in `catalog.md`.
- `pin_sha256` checks the selected executable's bytes. It does not authenticate
  arguments, loaded files, child executables, or an untrusted registry's choice
  of executable. Registry-supplied local commands are therefore refused.
- On install, the spec is validated through `MCPServerSpec.from_config` — the
  same subprocess-injection / shell-metacharacter / url-scheme checks the kernel
  applies to hand-written config — so a hostile entry (`command` with a shell
  metacharacter, a non-http url) is rejected, not written.
- A directly configured local server still needs independent review and a
  suitable OS isolation boundary before it runs.

## 5. Code

`packages/maverick-core/maverick/mcp_registry.py`:

- `load_mcp_registry()` / `resolve_mcp(name)` → `catalog.load_catalog("mcp", …)`
  with the `[mcp_registries] indexes`.
- `spec_from_entry(entry)` → a validated `MCPServerSpec` from `entry.spec`.
- `install_mcp_from_registry(name)` → resolve + validate, returns the spec (pure;
  the caller writes config, so a preview/dry-run is possible).
- `add_mcp_server_to_config(name, spec_dict)` / `remove_mcp_server_from_config(name)`
  — dependency-free TOML mutation (append the one table on add; text-scan removal
  on remove) so the rest of a hand-edited `config.toml` — comments, ordering,
  unrelated tables — is preserved. (`tomli_w` is not a dependency; Python ships no
  stdlib TOML writer.)

`MCPServerSpec.to_dict()` (in `mcp_client.py`) is the inverse of `from_config`,
used to serialize a resolved spec into config.

## 6. CLI

`maverick mcp-registry` (a group distinct from `maverick mcp`, which starts
Lightwork's *own* server):

- `browse` — list registry servers (name, version, transport, verified badge).
- `add <name>` — resolve + validate + write `[mcp_servers.<name>]`; loads on the
  next run. Refuses to overwrite an existing entry.
- `remove <name>` — drop the table from config.
- `list` — show the MCP servers currently configured (via the kernel's loader).

## 7. Config knob + wizard (kernel rules 5 and 6)

- Knob: `[mcp_registries] indexes` (list of base URLs). Discovery works with **no
  config** (built-in default), so the knob is only for custom/self-hosted
  registries.
- Wizard: `write_config` emits `[mcp_registries]` when custom indexes are
  supplied, so the installer can write the knob; it's omitted by default to keep
  the config minimal.

## 8. Deferred

- **OAuth 2.1** for remote servers (the other half of B2) — needs real accounts.
- **Signed registry entries** (Ed25519, mirroring the skills `trusted_pubkeys`
  path) — authenticity and complete workload authorization remain future work.
- **`install_count` / ranking** — the schema carries the field; surfacing it is
  cosmetic.

## Local command trust

The registry may describe local stdio servers for discovery, but installation
and probing of registry-supplied local commands are disabled: pinning an
executable does not authenticate its arguments or additional programs it may
run. An administrator can independently review and configure a local MCP
server directly. HTTP registry entries retain their separate network trust
requirements.
